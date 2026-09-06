$ErrorActionPreference='Stop'
$requestFile=$env:KAYDBOOKS_SDK_REQUEST
$outputFile=$env:KAYDBOOKS_SDK_RESPONSE
if (-not $requestFile -or -not $outputFile) {throw 'Private IPC paths required'}
$evidence=Join-Path (Split-Path $outputFile) ('sdk-exchange-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$dll=Join-Path ${env:CommonProgramFiles(x86)} 'Intuit\QuickBooks\Interop.QBXMLRP2.dll'
Add-Type -ReferencedAssemblies @($dll,'System.Xml.dll') -TypeDefinition @"
using System;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;
using Interop.QBXMLRP2;
public static class PrivateReadOnlyDiscovery {
 static void Save(string dir,string name,string value) {
  using(var file=new FileStream(Path.Combine(dir,name),FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
   var bytes=Encoding.UTF8.GetBytes(value); file.Write(bytes,0,bytes.Length); file.Flush(true);
  }
 }
 public static void Run(string dir, string requestFile, string outputFile) {
  IRequestProcessor4 rp=null; string ticket=null; bool opened=false; string response=null;
  Save(dir,"started.txt",DateTime.UtcNow.ToString("o")+" direct SDK diagnostic; transport evidence; binding validation required");
  try {
   rp=(IRequestProcessor4)new RequestProcessor2Class();
   var prefs=rp.AuthPreferences;
   prefs.PutIsReadOnly(true);
   prefs.PutUnattendedModePref(QBXMLRPUnattendedModePrefType.umpOptional);
   prefs.PutPersonalDataPref(QBXMLRPPersonalDataPrefType.pdpNotNeeded);
   rp.OpenConnection2("", "KaydBooks Bridge Direct Read-Only Diagnostic", QBXMLRPConnectionType.localQBD);
   opened=true;
   ticket=rp.BeginSession("",QBFileMode.qbFileOpenSingleUser);
   if(!prefs.GetIsReadOnly(ticket) || prefs.GetPersonalDataPref(ticket)!=QBXMLRPPersonalDataPrefType.pdpNotNeeded)
    throw new InvalidOperationException("Requested read-only and no-personal-data permissions were not granted");
   Save(dir,"permissions.txt","ReadOnly=true; PersonalData=pdpNotNeeded; binding validation required");
   string[] versions=rp.get_QBXMLVersionsForSession(ticket);
   Save(dir,"versions.txt",String.Join("\n",versions));
   if(Array.IndexOf(versions,"17.0")<0) throw new InvalidOperationException("Reviewed qbXML 17.0 not supported by this session");
   string request=File.ReadAllText(requestFile);
   var settings=new System.Xml.XmlReaderSettings(); settings.DtdProcessing=System.Xml.DtdProcessing.Prohibit; settings.XmlResolver=null;
   var doc=new System.Xml.XmlDocument(); doc.XmlResolver=null;
   using(var reader=System.Xml.XmlReader.Create(new StringReader(request),settings)) doc.Load(reader);
   var root=doc.DocumentElement;
   if(root.Name!="QBXML" || root.ChildNodes.Count!=1) throw new InvalidOperationException("Invalid discovery envelope");
   var batch=root.FirstChild;
   if(batch.Name!="QBXMLMsgsRq" || (batch.ChildNodes.Count!=2 && batch.ChildNodes.Count!=3)) throw new InvalidOperationException("Invalid discovery batch");
   string[] names={"HostQueryRq","CompanyQueryRq"};
   for(int i=0;i<2;i++) {
    var node=batch.ChildNodes[i];
    if(node.Name!=names[i] || node.HasChildNodes || node.Attributes.Count!=1 || node.Attributes["requestID"]==null ||
       !System.Text.RegularExpressions.Regex.IsMatch(node.Attributes["requestID"].Value,"^[0-9]+$"))
      throw new InvalidOperationException("Only fixed read-only discovery requests are permitted");
   }
   if(batch.ChildNodes.Count==3) {
    var account=batch.ChildNodes[2];
    string expected="<MaxReturned>20</MaxReturned><ActiveStatus>ActiveOnly</ActiveStatus><IncludeRetElement>ListID</IncludeRetElement><IncludeRetElement>FullName</IncludeRetElement><IncludeRetElement>AccountType</IncludeRetElement><IncludeRetElement>IsActive</IncludeRetElement>";
    if(account.Name!="AccountQueryRq" || account.Attributes.Count!=1 || account.Attributes["requestID"]==null ||
       !System.Text.RegularExpressions.Regex.IsMatch(account.Attributes["requestID"].Value,"^[0-9]+$") || account.InnerXml!=expected)
     throw new InvalidOperationException("Only bounded active-account preview is permitted");
   }
   Save(dir,"request.xml",request);
   Save(dir,"dispatch-intent.txt",DateTime.UtcNow.ToString("o"));
   response=rp.ProcessRequest(ticket,request);
   Save(dir,"response.xml",response);
   Save(dir,"response-received.txt",DateTime.UtcNow.ToString("o"));
  } catch(Exception ex) {Save(dir,"error.txt",ex.ToString()); throw;}
  finally {
   if(rp!=null) {
    try {if(ticket!=null)rp.EndSession(ticket);}
    finally {try{if(opened)rp.CloseConnection();}finally{Marshal.FinalReleaseComObject(rp);}}
   }
   Save(dir,"closed.txt",DateTime.UtcNow.ToString("o"));
  }
  if(response!=null) {
   string pending=outputFile+Guid.NewGuid().ToString("N")+".pending";
   Save(Path.GetDirectoryName(pending),Path.GetFileName(pending),response);
   File.Move(pending,outputFile);
  }
 }
}
"@
$mutex = New-Object System.Threading.Mutex($false, 'Global\KaydBooksBridgeReadOnlySDK')
$held = $false
try {
 try { $held = $mutex.WaitOne(0) }
 catch [System.Threading.AbandonedMutexException] { $held = $true }
 if (-not $held) { throw 'Another native SDK exchange is active' }
 [PrivateReadOnlyDiscovery]::Run($evidence,$requestFile,$outputFile)
} catch { exit 1 }
finally {
 if ($held) { $mutex.ReleaseMutex() }
 $mutex.Dispose()
}

