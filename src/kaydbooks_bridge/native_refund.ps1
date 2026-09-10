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
public static class ControlledSampleRefund {
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
 static string Id(XmlNode n) {
  if(n==null||n.Attributes.Count!=0||n.ChildNodes.Count!=1||n.FirstChild.NodeType!=XmlNodeType.Text||!System.Text.RegularExpressions.Regex.IsMatch(n.InnerText,@"\A[A-Za-z0-9-]{1,31}\z"))throw new Exception("Exact identity required");
  return n.InnerText;
 }
 public static void CheckWrite(string xml,string expected) {
  if(Hash(xml)!=expected)throw new Exception("Write snapshot changed");
  var doc=Parse(xml);var root=doc.DocumentElement;
  if(root.Name!="QBXML"||root.Attributes.Count!=0||root.ChildNodes.Count!=1)throw new Exception("Invalid refund envelope");
  var batch=root.FirstChild;
  if(batch.Name!="QBXMLMsgsRq"||batch.Attributes.Count!=1||batch.Attributes["onError"]==null||batch.Attributes["onError"].Value!="stopOnError"||batch.ChildNodes.Count!=1)throw new Exception("One refund required");
  var rq=batch.FirstChild;
  if(rq.Name!="ARRefundCreditCardAddRq"||rq.Attributes.Count!=1||rq.Attributes["requestID"]==null||!System.Text.RegularExpressions.Regex.IsMatch(rq.Attributes["requestID"].Value,@"\A[1-9][0-9]{0,18}\z")||rq.ChildNodes.Count!=1||rq.FirstChild.Name!="ARRefundCreditCardAdd")throw new Exception("Fixed recorded refund required");
  var add=rq.FirstChild;string shape="";
  foreach(string field in new string[]{"CustomerRef","RefundFromAccountRef","ARAccountRef"})shape+="<"+field+"><ListID>"+Id(add.SelectSingleNode(field+"/ListID"))+"</ListID></"+field+">";
  var date=add.SelectSingleNode("TxnDate");var reference=add.SelectSingleNode("RefNumber");
  if(date==null||reference==null||!System.Text.RegularExpressions.Regex.IsMatch(date.InnerText,@"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\z")||!System.Text.RegularExpressions.Regex.IsMatch(reference.InnerText,@"\A[A-Za-z0-9-]{1,11}\z"))throw new Exception("Fixed refund date/reference required");
  shape+="<TxnDate>"+date.InnerText+"</TxnDate><RefNumber>"+reference.InnerText+"</RefNumber><PaymentMethodRef><ListID>"+Id(add.SelectSingleNode("PaymentMethodRef/ListID"))+"</ListID></PaymentMethodRef>";
  var allocations=add.SelectNodes("RefundAppliedToTxnAdd");
  if(allocations.Count<1||allocations.Count>20)throw new Exception("Bounded refund allocations required");
  var seen=new System.Collections.Generic.HashSet<string>();
  foreach(XmlNode a in allocations) {
   string id=Id(a.SelectSingleNode("TxnID"));var value=a.SelectSingleNode("RefundAmount");decimal amount;
   if(!seen.Add(id)||value==null||!System.Text.RegularExpressions.Regex.IsMatch(value.InnerText,@"\A[0-9]+\.[0-9]{2}\z")||!decimal.TryParse(value.InnerText,System.Globalization.NumberStyles.AllowDecimalPoint,System.Globalization.CultureInfo.InvariantCulture,out amount)||amount<=0)throw new Exception("Exact positive refund amount required");
   shape+="<RefundAppliedToTxnAdd><TxnID>"+id+"</TxnID><RefundAmount>"+value.InnerText+"</RefundAmount></RefundAppliedToTxnAdd>";
  }
  if(add.Attributes.Count!=0||add.InnerXml!=shape)throw new Exception("Only a recorded refund is permitted; no card processor information or extra fields");
 }
 public static void Run(string root,string hash,bool readOnly) {
  IRequestProcessor4 rp=null;string ticket=null;bool opened=false;
  try {
   string request=File.ReadAllText(Path.Combine(root,"preflight.request.xml"));
   var batch=Parse(request).SelectSingleNode("/QBXML/QBXMLMsgsRq");
   if(batch==null||batch.ChildNodes.Count<3)throw new Exception("Preflight required");
   foreach(XmlNode q in batch.ChildNodes)
    if(Array.IndexOf(new string[]{"HostQueryRq","CompanyQueryRq","PreferencesQueryRq","AccountQueryRq","CustomerQueryRq","ItemServiceQueryRq","ItemInventoryQueryRq","SalesTaxCodeQueryRq","InvoiceQueryRq","CreditMemoQueryRq","PaymentMethodQueryRq","ARRefundCreditCardQueryRq"},q.Name)<0)throw new Exception("Unsupported preflight request");
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
 [ControlledSampleRefund]::Run($env:KAYDBOOKS_NATIVE_DIRECTORY,$env:KAYDBOOKS_NATIVE_REQUEST_HASH,($env:KAYDBOOKS_NATIVE_READ_ONLY -eq 'true'))
} catch {exit 1} finally {if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
