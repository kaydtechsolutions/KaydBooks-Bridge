$ErrorActionPreference='Stop'
$dll=Join-Path ${env:CommonProgramFiles(x86)} 'Intuit\QuickBooks\Interop.QBXMLRP2.dll'
Add-Type -ReferencedAssemblies @($dll,'System.Xml.dll') -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Xml;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using Interop.QBXMLRP2;
public static class ControlledSampleSupplierCredit {
 static void Save(string root,string name,string text) {
  string pending=Path.Combine(root,name+".pending");
  using(var f=new FileStream(pending,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
   byte[] bytes=Encoding.UTF8.GetBytes(text);f.Write(bytes,0,bytes.Length);f.Flush(true);
  }
  File.Move(pending,Path.Combine(root,name));
 }
 static XmlDocument Parse(string value) {
  var settings=new XmlReaderSettings();settings.DtdProcessing=DtdProcessing.Prohibit;settings.XmlResolver=null;
  var doc=new XmlDocument();doc.XmlResolver=null;
  using(var reader=XmlReader.Create(new StringReader(value),settings))doc.Load(reader);
  return doc;
 }
 public static string Hash(string value) {
  using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-","").ToLowerInvariant();
 }
 static string Text(XmlNode n) {
  if(n.Attributes.Count!=0||n.ChildNodes.Count!=1||n.FirstChild.NodeType!=XmlNodeType.Text)throw new Exception("Scalar required");
  return n.InnerText;
 }
 static void Ref(XmlNode n) {
  if(n.Attributes.Count!=0||n.ChildNodes.Count!=1||n.FirstChild.Name!="ListID"||!System.Text.RegularExpressions.Regex.IsMatch(Text(n.FirstChild),@"\A[A-Za-z0-9-]{1,31}\z"))throw new Exception("Exact ListID required");
 }
 public static void CheckWrite(string xml,string expected) {
  if(Hash(xml)!=expected)throw new Exception("Write snapshot changed");
  var doc=Parse(xml);var root=doc.DocumentElement;
  if(root.Name!="QBXML"||root.Attributes.Count!=0||root.ChildNodes.Count!=1)throw new Exception("Invalid envelope");
  var batch=root.FirstChild;
  if(batch.Name!="QBXMLMsgsRq"||batch.Attributes.Count!=1||batch.Attributes["onError"]==null||batch.Attributes["onError"].Value!="stopOnError"||batch.ChildNodes.Count!=1)throw new Exception("One bill required");
  var rq=batch.FirstChild;
  if(rq.Name!="VendorCreditAddRq"||rq.Attributes.Count!=1||rq.Attributes["requestID"]==null||!System.Text.RegularExpressions.Regex.IsMatch(rq.Attributes["requestID"].Value,@"\A[1-9][0-9]{0,18}\z")||rq.ChildNodes.Count!=1||rq.FirstChild.Name!="VendorCreditAdd")throw new Exception("One BillAdd required");
  var bill=rq.FirstChild;
  if(bill.Attributes.Count!=0||bill.ChildNodes.Count<6||bill.ChildNodes.Count>106)throw new Exception("Invalid bill fields");
  string[] fields={"VendorRef","APAccountRef","TxnDate","RefNumber","Memo"};
  for(int i=0;i<5;i++)if(bill.ChildNodes[i].Name!=fields[i])throw new Exception("Invalid bill field order");
  Ref(bill.ChildNodes[0]);Ref(bill.ChildNodes[1]);
  DateTime date;
  if(!DateTime.TryParseExact(Text(bill.ChildNodes[2]),"yyyy-MM-dd",System.Globalization.CultureInfo.InvariantCulture,System.Globalization.DateTimeStyles.None,out date))throw new Exception("Invalid date");
  if(!System.Text.RegularExpressions.Regex.IsMatch(Text(bill.ChildNodes[3]),@"\A[A-Za-z0-9-]{1,11}\z"))throw new Exception("Invalid reference");
  if(!System.Text.RegularExpressions.Regex.IsMatch(Text(bill.ChildNodes[4]),@"\AKaydBooks bill [A-Za-z0-9-]{1,31}\z"))throw new Exception("Exact original bill required");
  int firstLine=5;
  if(bill.ChildNodes.Count<=firstLine||bill.ChildNodes.Count-firstLine>100) throw new Exception("One to 100 expense lines required");
  bool seenItem=false;
  for(int i=firstLine;i<bill.ChildNodes.Count;i++) {
   var line=bill.ChildNodes[i];
   if(line.Name=="ItemLineAdd") {
    seenItem=true;
    if(line.Attributes.Count!=0||line.ChildNodes.Count!=4||line.ChildNodes[0].Name!="ItemRef"||line.ChildNodes[1].Name!="Quantity"||line.ChildNodes[2].Name!="Cost"||line.ChildNodes[3].Name!="Amount") throw new Exception("Fixed service item fields required");
    Ref(line.ChildNodes[0]);
    for(int j=1;j<4;j++) {
     decimal val; string text=Text(line.ChildNodes[j]);
     string pattern=j==1?@"\A(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?\z":@"\A[0-9]+\.[0-9]{2}\z";
     if(!System.Text.RegularExpressions.Regex.IsMatch(text,pattern)||!decimal.TryParse(text,System.Globalization.NumberStyles.AllowDecimalPoint,System.Globalization.CultureInfo.InvariantCulture,out val)||val<=0)throw new Exception("Invalid service cost or quantity");
    }
    continue;
   }
   if(seenItem)throw new Exception("Expense lines must precede items");
   if(line.Name!="ExpenseLineAdd"||line.Attributes.Count!=0||line.ChildNodes.Count!=2||line.ChildNodes[0].Name!="AccountRef"||line.ChildNodes[1].Name!="Amount")throw new Exception("Only expense lines permitted");
   Ref(line.ChildNodes[0]);decimal amount;
   string value=Text(line.ChildNodes[1]);
   if(!System.Text.RegularExpressions.Regex.IsMatch(value,@"\A[0-9]+\.[0-9]{2}\z")||!decimal.TryParse(value,System.Globalization.NumberStyles.AllowDecimalPoint,System.Globalization.CultureInfo.InvariantCulture,out amount)||amount<=0)throw new Exception("Invalid expense line");
  }
 }
 public static void Run(string root,string hash,bool readOnly) {
  IRequestProcessor4 rp=null;string ticket=null;bool opened=false;
  try {
   string request=File.ReadAllText(Path.Combine(root,"preflight.request.xml"));
   var batch=Parse(request).SelectSingleNode("/QBXML/QBXMLMsgsRq");
   if(batch==null||batch.ChildNodes.Count<3)throw new Exception("Preflight required");
   foreach(XmlNode q in batch.ChildNodes)
    if(Array.IndexOf(new string[]{"HostQueryRq","CompanyQueryRq","PreferencesQueryRq","AccountQueryRq","VendorQueryRq","BillQueryRq","VendorCreditQueryRq","BillToPayQueryRq","StandardTermsQueryRq","ItemServiceQueryRq","ItemInventoryQueryRq"},q.Name)<0)throw new Exception("Unsupported preflight request");
   string write=readOnly?null:File.ReadAllText(Path.Combine(root,"write.request.xml"));
   if(!readOnly)CheckWrite(write,hash);
   rp=(IRequestProcessor4)new RequestProcessor2Class();var auth=rp.AuthPreferences;
   auth.PutIsReadOnly(readOnly);auth.PutPersonalDataPref(QBXMLRPPersonalDataPrefType.pdpNotNeeded);
   auth.PutUnattendedModePref(QBXMLRPUnattendedModePrefType.umpOptional);
   rp.OpenConnection2("",readOnly?"KaydBooks Bridge Direct Read-Only Diagnostic":"KaydBooks Bridge Controlled Sample Posting",QBXMLRPConnectionType.localQBD);opened=true;
   ticket=rp.BeginSession("",QBFileMode.qbFileOpenSingleUser);
   if(auth.GetIsReadOnly(ticket)!=readOnly||auth.GetPersonalDataPref(ticket)!=QBXMLRPPersonalDataPrefType.pdpNotNeeded)throw new Exception("Permissions differ");
   if(Array.IndexOf(rp.get_QBXMLVersionsForSession(ticket),"17.0")<0)throw new Exception("qbXML 17 required");
   Save(root,"preflight.response.xml",rp.ProcessRequest(ticket,request));
   if(readOnly)return;
   DateTime deadline=DateTime.UtcNow.AddSeconds(30);
   while(!File.Exists(Path.Combine(root,"authorize.txt"))) {
    if(File.Exists(Path.Combine(root,"cancel.txt")))return;
    if(DateTime.UtcNow>deadline)throw new Exception("Parent authorization expired");
    System.Threading.Thread.Sleep(50);
   }
   if(DateTime.UtcNow>deadline||File.ReadAllText(Path.Combine(root,"authorize.txt"))!=hash)throw new Exception("Invalid parent authorization");
   // The per-job fence survives parent/helper death and is never cleared for a retry.
   Save(root,"write-intent.txt",hash);
   Save(root,"add.response.xml",rp.ProcessRequest(ticket,write));
  } catch(Exception e) {Save(root,"error.txt",e.ToString());throw;}
  finally {
   if(rp!=null) {
    try {if(ticket!=null)rp.EndSession(ticket);}
    finally {try {if(opened)rp.CloseConnection();}finally {Marshal.FinalReleaseComObject(rp);}}
   }
   Save(root,"closed.txt",DateTime.UtcNow.ToString("o"));
  }
 }
}
'@
$mutex=New-Object System.Threading.Mutex($false,'Global\KaydBooksBridgeReadOnlySDK')
$held=$false
try {
 try {$held=$mutex.WaitOne(0)} catch [System.Threading.AbandonedMutexException] {$held=$true}
 if(-not $held){throw 'Another native exchange is active'}
 [ControlledSampleSupplierCredit]::Run($env:KAYDBOOKS_NATIVE_DIRECTORY,$env:KAYDBOOKS_NATIVE_REQUEST_HASH,($env:KAYDBOOKS_NATIVE_READ_ONLY -eq 'true'))
} catch {exit 1} finally {if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
