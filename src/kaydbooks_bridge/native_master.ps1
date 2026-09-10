$ErrorActionPreference='Stop'
$dll=Join-Path ${env:CommonProgramFiles(x86)} 'Intuit\QuickBooks\Interop.QBXMLRP2.dll'
Add-Type -ReferencedAssemblies @($dll,'System.Xml.dll','System.Core.dll') -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Xml;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using Interop.QBXMLRP2;
public static class ControlledSampleMaster {
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
 static bool OneOf(string value,string list) {return Array.IndexOf(list.Split(','),value)>=0;}
 static void Fields(XmlNode node,string allowed,bool refs) {
  var seen=new System.Collections.Generic.HashSet<string>();
  foreach(XmlNode child in node.ChildNodes) {
   if(!seen.Add(child.Name)||!OneOf(child.Name,allowed)||child.Attributes.Count!=0)throw new Exception("Unsupported master field");
   if(child.Name.EndsWith("Ref")) {
    if(!refs||child.ChildNodes.Count!=1||child.FirstChild.Name!="ListID"||child.FirstChild.ChildNodes.Count!=1||child.FirstChild.FirstChild.NodeType!=XmlNodeType.Text)throw new Exception("Exact account reference required");
   } else if(child.HasChildNodes&&(child.ChildNodes.Count!=1||child.FirstChild.NodeType!=XmlNodeType.Text))throw new Exception("Scalar master field required");
  }
 }
 public static void CheckWrite(string xml,string expected) {
  if(Hash(xml)!=expected)throw new Exception("Write snapshot changed");
  var doc=Parse(xml);var batch=doc.SelectSingleNode("/QBXML/QBXMLMsgsRq");
  if(batch==null||batch.ChildNodes.Count!=1)throw new Exception("One master write required");
  var rq=batch.FirstChild;
  if(!OneOf(rq.Name,"CustomerAddRq,CustomerModRq,VendorAddRq,VendorModRq,ItemServiceAddRq,ItemServiceModRq,ItemInventoryAddRq,ItemInventoryModRq,ItemDiscountAddRq,ItemDiscountModRq,ItemOtherChargeAddRq,ItemOtherChargeModRq")||rq.ChildNodes.Count!=1||rq.FirstChild.Name!=rq.Name.Substring(0,rq.Name.Length-2))throw new Exception("Unsupported master request");
  var node=rq.FirstChild;bool add=rq.Name.EndsWith("AddRq");
  string common=add?"Name,IsActive,ExternalGUID":"ListID,EditSequence,Name,IsActive";
  if(!add&&(node.SelectSingleNode("ListID")==null||node.SelectSingleNode("EditSequence")==null))throw new Exception("Modification identity required");
  if(add&&(node.SelectSingleNode("Name")==null||node.SelectSingleNode("ExternalGUID")==null))throw new Exception("Creation identity required");
  if(rq.Name.StartsWith("Customer")||rq.Name.StartsWith("Vendor"))Fields(node,common+",CompanyName,Phone,Email",false);
  else if(rq.Name.StartsWith("ItemInventory"))Fields(node,common+",SalesDesc,SalesPrice,PurchaseDesc,PurchaseCost"+(add?",IncomeAccountRef,COGSAccountRef,AssetAccountRef":""),add);
  else if(rq.Name.StartsWith("ItemDiscount"))Fields(node,common+",ItemDesc,DiscountRate"+(add?",AccountRef":""),add);
  else {
   string aggregate=null;
   foreach(XmlNode child in node.ChildNodes)if(child.Name.StartsWith("Sales")) {
    if(aggregate!=null||!OneOf(child.Name,add?"SalesOrPurchase,SalesAndPurchase":"SalesOrPurchaseMod,SalesAndPurchaseMod"))throw new Exception("Unsupported service aggregate");
    aggregate=child.Name;
    bool purchased=child.Name.StartsWith("SalesAnd");
    Fields(child,purchased?"SalesDesc,SalesPrice,PurchaseDesc,PurchaseCost"+(add?",IncomeAccountRef,ExpenseAccountRef":""):"Desc,Price"+(add?",AccountRef":""),add);
   }
   var copy=node.CloneNode(true);if(aggregate!=null)copy.RemoveChild(copy.SelectSingleNode(aggregate));Fields(copy,common,false);
  }
 }
 public static void Run(string root,string hash,bool readOnly) {
  IRequestProcessor4 rp=null;string ticket=null;bool opened=false;
  try {
   string request=File.ReadAllText(Path.Combine(root,"preflight.request.xml"));
   var batch=Parse(request).SelectSingleNode("/QBXML/QBXMLMsgsRq");
   if(batch==null||batch.ChildNodes.Count<2||batch.ChildNodes.Count>12)throw new Exception("Bounded read batch required");
   foreach(XmlNode q in batch.ChildNodes) {
    if(!OneOf(q.Name,"HostQueryRq,CompanyQueryRq,PreferencesQueryRq,AccountQueryRq,CustomerQueryRq,VendorQueryRq,ItemServiceQueryRq,ItemInventoryQueryRq,ItemDiscountQueryRq,ItemOtherChargeQueryRq,EntityQueryRq,ItemQueryRq"))throw new Exception("Unsupported preflight request");
    foreach(XmlNode f in q.ChildNodes)if(!OneOf(f.Name,"ListID,FullName,IncludeRetElement")||f.ChildNodes.Count!=1||f.FirstChild.NodeType!=XmlNodeType.Text)throw new Exception("Fixed read selectors required");
   }
   string write=readOnly?null:File.ReadAllText(Path.Combine(root,"write.request.xml"));if(!readOnly)CheckWrite(write,hash);
   rp=(IRequestProcessor4)new RequestProcessor2Class();var auth=rp.AuthPreferences;
   auth.PutIsReadOnly(readOnly);auth.PutPersonalDataPref(QBXMLRPPersonalDataPrefType.pdpNotNeeded);
   auth.PutUnattendedModePref(QBXMLRPUnattendedModePrefType.umpOptional);
   rp.OpenConnection2("",readOnly?"KaydBooks Bridge Direct Read-Only Diagnostic":"KaydBooks Bridge Controlled Sample Posting",QBXMLRPConnectionType.localQBD);opened=true;
   ticket=rp.BeginSession("",QBFileMode.qbFileOpenSingleUser);
   if(auth.GetIsReadOnly(ticket)!=readOnly||auth.GetPersonalDataPref(ticket)!=QBXMLRPPersonalDataPrefType.pdpNotNeeded)throw new Exception("Permissions differ");
   if(Array.IndexOf(rp.get_QBXMLVersionsForSession(ticket),"17.0")<0)throw new Exception("qbXML 17 required");
   Save(root,"preflight.response.xml",rp.ProcessRequest(ticket,request));if(readOnly)return;
   DateTime deadline=DateTime.UtcNow.AddSeconds(30);
   while(!File.Exists(Path.Combine(root,"authorize.txt"))) {
    if(File.Exists(Path.Combine(root,"cancel.txt")))return;
    if(DateTime.UtcNow>deadline)throw new Exception("Parent authorization expired");System.Threading.Thread.Sleep(50);
   }
   if(DateTime.UtcNow>deadline||File.ReadAllText(Path.Combine(root,"authorize.txt"))!=hash)throw new Exception("Invalid parent authorization");
   Save(root,"write-intent.txt",hash);Save(root,"add.response.xml",rp.ProcessRequest(ticket,write));
  } catch(Exception e) {Save(root,"error.txt",e.ToString());throw;}
  finally {
   if(rp!=null) {try {if(ticket!=null)rp.EndSession(ticket);}finally {try {if(opened)rp.CloseConnection();}finally {Marshal.FinalReleaseComObject(rp);}}}
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
 [ControlledSampleMaster]::Run($env:KAYDBOOKS_NATIVE_DIRECTORY,$env:KAYDBOOKS_NATIVE_REQUEST_HASH,($env:KAYDBOOKS_NATIVE_READ_ONLY -eq 'true'))
} catch {exit 1} finally {if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
