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
public static class ControlledSamplePayment {
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
 static void CheckAllocation(XmlNode n) {
  if(n.Name!="AppliedToTxnAdd"||n.Attributes.Count!=0||(n.ChildNodes.Count!=2&&n.ChildNodes.Count!=4))throw new Exception("Explicit payment allocation required");
  string[] fields={"TxnID","PaymentAmount","DiscountAmount"};
  int scalars=n.ChildNodes.Count==4?3:2;
  for(int i=0;i<scalars;i++) {
   var child=n.ChildNodes[i];
   if(child.Name!=fields[i]||child.Attributes.Count!=0||child.ChildNodes.Count!=1||child.FirstChild.NodeType!=XmlNodeType.Text)throw new Exception("Exact scalar allocation required");
   string pattern=i==0?@"\A[A-Za-z0-9-]{1,31}\z":@"\A(?:0|[1-9][0-9]{0,11})\.[0-9]{2}\z";
   if(!System.Text.RegularExpressions.Regex.IsMatch(child.InnerText,pattern))throw new Exception("Invalid allocation value");
  }
  if(scalars==3) {
   var account=n.ChildNodes[3];
   if(n.ChildNodes[2].InnerText=="0.00"||account.Name!="DiscountAccountRef"||account.Attributes.Count!=0||account.ChildNodes.Count!=1||account.FirstChild.Name!="ListID"||account.FirstChild.Attributes.Count!=0||account.FirstChild.ChildNodes.Count!=1||account.FirstChild.FirstChild.NodeType!=XmlNodeType.Text||!System.Text.RegularExpressions.Regex.IsMatch(account.InnerText,@"\A[A-Za-z0-9-]{1,31}\z"))throw new Exception("Exact settlement discount account required");
  }
 }
 public static void CheckWrite(string xml,string expected) {
  if(Hash(xml)!=expected)throw new Exception("Write snapshot changed");
  var doc=Parse(xml);var batch=doc.SelectSingleNode("/QBXML/QBXMLMsgsRq");
  if(batch==null||batch.ChildNodes.Count!=1||batch.FirstChild.Name!="ReceivePaymentAddRq"||batch.FirstChild.ChildNodes.Count!=1||batch.FirstChild.FirstChild.Name!="ReceivePaymentAdd")throw new Exception("Only one customer payment add permitted");
  var payment=batch.FirstChild.FirstChild;
  string[] fields={"CustomerRef","ARAccountRef","TxnDate","RefNumber","TotalAmount","PaymentMethodRef","DepositToAccountRef"};
  if(payment.ChildNodes.Count<8||payment.ChildNodes.Count>27)throw new Exception("Explicit payment fields and allocations required");
  for(int i=0;i<7;i++) {
   var n=payment.ChildNodes[i];if(n.Name!=fields[i]||n.Attributes.Count!=0)throw new Exception("Payment field order differs");
   if(n.Name.EndsWith("Ref")) {
    if(n.ChildNodes.Count!=1||n.FirstChild.Name!="ListID"||n.FirstChild.Attributes.Count!=0||n.FirstChild.ChildNodes.Count!=1||n.FirstChild.FirstChild.NodeType!=XmlNodeType.Text||!System.Text.RegularExpressions.Regex.IsMatch(n.FirstChild.InnerText,@"\A[A-Za-z0-9-]{1,31}\z"))throw new Exception("Exact payment master required");
   } else if(n.ChildNodes.Count!=1||n.FirstChild.NodeType!=XmlNodeType.Text)throw new Exception("Scalar payment field required");
  }
  if(!System.Text.RegularExpressions.Regex.IsMatch(payment.ChildNodes[4].InnerText,@"\A(?:0|[1-9][0-9]{0,11})\.[0-9]{2}\z"))throw new Exception("Invalid payment total");
  for(int i=7;i<payment.ChildNodes.Count;i++) {
   var n=payment.ChildNodes[i];
   if(n.Name=="IsAutoApply"&&n.Attributes.Count==0&&payment.ChildNodes.Count==8&&n.InnerXml=="false")continue;
   CheckAllocation(n);
  }
 }

 public static void Run(string root,string hash,bool readOnly) {
  IRequestProcessor4 rp=null;string ticket=null;bool opened=false;
  try {
   string request=File.ReadAllText(Path.Combine(root,"preflight.request.xml"));
   var batch=Parse(request).SelectSingleNode("/QBXML/QBXMLMsgsRq");
   if(batch==null||(batch.ChildNodes.Count<8||batch.ChildNodes.Count>29))throw new Exception("Preflight required");
   foreach(XmlNode q in batch.ChildNodes)
    if(Array.IndexOf(new string[]{"HostQueryRq","CompanyQueryRq","PreferencesQueryRq","AccountQueryRq","CustomerQueryRq","PaymentMethodQueryRq","InvoiceQueryRq","ReceivePaymentQueryRq"},q.Name)<0)throw new Exception("Unsupported preflight request");
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
 [ControlledSamplePayment]::Run($env:KAYDBOOKS_NATIVE_DIRECTORY,$env:KAYDBOOKS_NATIVE_REQUEST_HASH,($env:KAYDBOOKS_NATIVE_READ_ONLY -eq 'true'))
} catch {exit 1} finally {if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
