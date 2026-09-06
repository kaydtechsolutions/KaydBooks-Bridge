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
 static void FixedQuery(System.Xml.XmlNode node,string name,string fields,bool exact,bool preview=false) {
  if(node.Name!=name+"QueryRq" || node.Attributes.Count!=1 || node.Attributes["requestID"]==null ||
     !System.Text.RegularExpressions.Regex.IsMatch(node.Attributes["requestID"].Value,@"\A[0-9]+\z"))
   throw new InvalidOperationException("Invalid fixed master query");
  string expected="";
  if(exact) {
   var id=node.FirstChild;
   if(id==null || id.Name!="ListID" || id.Attributes.Count!=0 || id.ChildNodes.Count!=1 ||
      id.FirstChild.NodeType!=System.Xml.XmlNodeType.Text ||
      !System.Text.RegularExpressions.Regex.IsMatch(id.InnerText,@"\A[A-Za-z0-9-]{1,31}\z"))
    throw new InvalidOperationException("Exact master selector required");
   expected="<ListID>"+id.InnerText+"</ListID>";
  }
  if(preview) expected="<MaxReturned>20</MaxReturned><ActiveStatus>ActiveOnly</ActiveStatus>";
  foreach(string field in fields.Split(',')) expected+="<IncludeRetElement>"+field+"</IncludeRetElement>";
  if(node.InnerXml!=expected) throw new InvalidOperationException("Only fixed projected master fields permitted");
 }
 static void CommercialQuery(System.Xml.XmlNode node) {
  bool preview=node.FirstChild!=null && node.FirstChild.Name=="MaxReturned";
  switch(node.Name) {
   case "PreferencesQueryRq": FixedQuery(node,"Preferences","MultiCurrencyPreferences,SalesTaxPreferences,SalesAndCustomersPreferences,PurchasesAndVendorsPreferences,MultiLocationInventoryPreferences,ItemsAndInventoryPreferences",false); break;
   case "CurrencyQueryRq": FixedQuery(node,"Currency","ListID,IsActive,CurrencyCode",!preview,preview); break;
   case "AccountQueryRq": FixedQuery(node,"Account","ListID,IsActive,AccountType,CurrencyRef",!preview,preview); break;
   case "CustomerQueryRq": FixedQuery(node,"Customer","ListID,IsActive,CurrencyRef,SalesTaxCodeRef,ItemSalesTaxRef,PriceLevelRef",!preview,preview); break;
   case "ItemServiceQueryRq": FixedQuery(node,"ItemService","ListID,IsActive,SalesOrPurchase,SalesAndPurchase,SalesTaxCodeRef,UnitOfMeasureSetRef,IsTaxIncluded",!preview,preview); break;
   case "ItemInventoryQueryRq": FixedQuery(node,"ItemInventory","ListID,IsActive,SalesPrice,IncomeAccountRef,COGSAccountRef,AssetAccountRef,QuantityOnHand,QuantityOnSalesOrder,SalesTaxCodeRef,UnitOfMeasureSetRef,IsTaxIncluded",!preview,preview); break;
   case "SalesTaxCodeQueryRq": FixedQuery(node,"SalesTaxCode","ListID,IsActive,IsTaxable",!preview,preview); break;
   case "ItemSalesTaxQueryRq": FixedQuery(node,"ItemSalesTax","ListID,IsActive,TaxRate",!preview,preview); break;
   default: throw new InvalidOperationException("Unsupported commercial query");
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
   bool billPreview=batch.ChildNodes.Count==5 && batch.ChildNodes[2].Name=="PreferencesQueryRq" && batch.ChildNodes[3].Name=="VendorQueryRq";
   bool billCheck=batch.ChildNodes.Count>=6 && batch.ChildNodes.Count<=106 && batch.ChildNodes[2].Name=="PreferencesQueryRq" && batch.ChildNodes[3].Name=="VendorQueryRq";
   bool commercial=batch.ChildNodes.Count>=8 && batch.ChildNodes.Count<=88 && batch.ChildNodes[2].Name=="PreferencesQueryRq" && batch.ChildNodes[2].InnerXml.Contains("<IncludeRetElement>SalesTaxPreferences</IncludeRetElement>");
   bool single=!commercial && batch.ChildNodes.Count>=7 && batch.ChildNodes.Count<=45 && batch.ChildNodes.Count%2==1 && batch.ChildNodes[3].Name=="AccountQueryRq";
   bool invoice=!commercial && !billCheck && (single || (batch.ChildNodes.Count>=8 && batch.ChildNodes.Count<=46 && batch.ChildNodes.Count%2==0));
   if(batch.Name!="QBXMLMsgsRq" || (batch.ChildNodes.Count!=2 && batch.ChildNodes.Count!=3 && batch.ChildNodes.Count!=7 && !invoice && !commercial && !billPreview && !billCheck)) throw new InvalidOperationException("Invalid discovery batch");
   string[] names={"HostQueryRq","CompanyQueryRq"};
   for(int i=0;i<2;i++) {
    var node=batch.ChildNodes[i];
    if(node.Name!=names[i] || node.HasChildNodes || node.Attributes.Count!=1 || node.Attributes["requestID"]==null ||
       !System.Text.RegularExpressions.Regex.IsMatch(node.Attributes["requestID"].Value,"^[0-9]+$"))
      throw new InvalidOperationException("Only fixed read-only discovery requests are permitted");
   }
   if(batch.ChildNodes.Count==3 && batch.ChildNodes[2].Name=="InvoiceQueryRq") {
    var query=batch.ChildNodes[2];
    var selector=query.FirstChild;
    if(query.Attributes.Count!=1 || query.Attributes["requestID"]==null ||
       !System.Text.RegularExpressions.Regex.IsMatch(query.Attributes["requestID"].Value,@"\A[1-9][0-9]{0,16}\z") ||
       selector==null || selector.Name!="TxnID" || selector.Attributes.Count!=0 ||
       selector.ChildNodes.Count!=1 || selector.FirstChild.NodeType!=System.Xml.XmlNodeType.Text ||
       !System.Text.RegularExpressions.Regex.IsMatch(selector.InnerText,@"\A[A-Za-z0-9-]{1,31}\z"))
     throw new InvalidOperationException("Exact invoice receipt selector required");
    string expected="<TxnID>"+selector.InnerText+"</TxnID><IncludeLineItems>true</IncludeLineItems><IncludeLinkedTxns>true</IncludeLinkedTxns>";
    foreach(string field in "TxnID,EditSequence,CustomerRef,ARAccountRef,TxnDate,RefNumber,IsPending,IsFinanceCharge,Subtotal,SalesTaxTotal,AppliedAmount,BalanceRemaining,CurrencyRef,ExchangeRate,IsPaid,IsToBePrinted,IsToBeEmailed,IsTaxIncluded,CustomerSalesTaxCodeRef,ItemSalesTaxRef,LinkedTxn,InvoiceLineRet,InvoiceLineGroupRet,DiscountLineRet,SalesTaxLineRet,ShippingLineRet".Split(','))
     expected+="<IncludeRetElement>"+field+"</IncludeRetElement>";
    if(query.InnerXml!=expected) throw new InvalidOperationException("Only fixed invoice receipt fields permitted");
   } else if(batch.ChildNodes.Count==3 && batch.ChildNodes[2].Name=="StandardTermsQueryRq") {
    FixedQuery(batch.ChildNodes[2],"StandardTerms","ListID,Name,IsActive,StdDueDays,StdDiscountDays,DiscountPct",false,true);
   } else if(batch.ChildNodes.Count==3 && batch.ChildNodes[2].Name=="BillQueryRq") {
    var query=batch.ChildNodes[2];var selector=query.FirstChild;
    if(query.Attributes.Count!=1 || query.Attributes["requestID"]==null ||
       !System.Text.RegularExpressions.Regex.IsMatch(query.Attributes["requestID"].Value,@"\A[1-9][0-9]{0,18}\z") ||
       selector==null || selector.Name!="TxnID" || selector.Attributes.Count!=0 || selector.ChildNodes.Count!=1 || selector.FirstChild.NodeType!=System.Xml.XmlNodeType.Text ||
       !System.Text.RegularExpressions.Regex.IsMatch(selector.InnerText,@"\A[A-Za-z0-9-]{1,31}\z")) throw new InvalidOperationException("Exact bill receipt selector required");
    string expected="<TxnID>"+selector.InnerText+"</TxnID><IncludeLineItems>true</IncludeLineItems><IncludeLinkedTxns>true</IncludeLinkedTxns>";
    foreach(string field in "TxnID,EditSequence,VendorRef,APAccountRef,TxnDate,DueDate,RefNumber,TermsRef,AmountDue,OpenAmount,CurrencyRef,ExchangeRate,AmountDueInHomeCurrency,IsPaid,IsTaxIncluded,SalesTaxCodeRef,LinkedTxn,ExpenseLineRet,ItemLineRet,ItemGroupLineRet".Split(',')) expected+="<IncludeRetElement>"+field+"</IncludeRetElement>";
    if(query.InnerXml!=expected) throw new InvalidOperationException("Only fixed bill receipt fields permitted");
   } else if(batch.ChildNodes.Count==3) {
    var account=batch.ChildNodes[2];
    string expected="<MaxReturned>20</MaxReturned><ActiveStatus>ActiveOnly</ActiveStatus><IncludeRetElement>ListID</IncludeRetElement><IncludeRetElement>FullName</IncludeRetElement><IncludeRetElement>AccountType</IncludeRetElement><IncludeRetElement>IsActive</IncludeRetElement>";
    if(account.SelectSingleNode("AccountType")!=null && account.SelectSingleNode("AccountType").InnerText=="Expense")
     expected=expected.Replace("</ActiveStatus>","</ActiveStatus><AccountType>Expense</AccountType>");
    if(account.FirstChild!=null && account.FirstChild.Name=="ListID") {
     var selector=account.FirstChild;
     if(selector.Attributes.Count!=0 || selector.ChildNodes.Count!=1 || selector.FirstChild.NodeType!=System.Xml.XmlNodeType.Text ||
        !System.Text.RegularExpressions.Regex.IsMatch(selector.InnerText,@"\A[A-Za-z0-9-]{1,31}\z"))
      throw new InvalidOperationException("Invalid exact account selector");
     expected="<ListID>"+selector.InnerText+"</ListID>"+expected.Substring(expected.IndexOf("<IncludeRetElement>"));
    }
    if(account.Name!="AccountQueryRq" || account.Attributes.Count!=1 || account.Attributes["requestID"]==null ||
       !System.Text.RegularExpressions.Regex.IsMatch(account.Attributes["requestID"].Value,"^[0-9]+$") || account.InnerXml!=expected)
     throw new InvalidOperationException("Only fixed account preview or exact-ID query is permitted");
   }
   if(billPreview) {
    FixedQuery(batch.ChildNodes[2],"Preferences","MultiCurrencyPreferences",false);
    FixedQuery(batch.ChildNodes[3],"Vendor","ListID,Name,IsActive,CurrencyRef",false,true);
    FixedQuery(batch.ChildNodes[4],"Account","ListID,FullName,IsActive,AccountType,CurrencyRef",false,true);
   }
   if(billCheck) {
    FixedQuery(batch.ChildNodes[2],"Preferences","MultiCurrencyPreferences",false);
    FixedQuery(batch.ChildNodes[3],"Vendor","ListID,Name,IsActive,CurrencyRef",true);
    int accountEnd=batch.ChildNodes.Count;
    if(batch.LastChild.Name=="StandardTermsQueryRq") {
     FixedQuery(batch.LastChild,"StandardTerms","ListID,Name,IsActive,StdDueDays,StdDiscountDays,DiscountPct",true);
     accountEnd--;
    }
    if(accountEnd<6||accountEnd>105) throw new InvalidOperationException("One to 100 expense accounts required");
    for(int i=4;i<accountEnd;i++) FixedQuery(batch.ChildNodes[i],"Account","ListID,FullName,IsActive,AccountType,CurrencyRef",true);
   }
   if(batch.ChildNodes.Count==7 && !single && !billCheck) {
    FixedQuery(batch.ChildNodes[2],"Preferences","MultiCurrencyPreferences",false);
    FixedQuery(batch.ChildNodes[3],"Currency","ListID,IsActive,CurrencyCode",false,true);
    FixedQuery(batch.ChildNodes[4],"Customer","ListID,IsActive,CurrencyRef",false,true);
    FixedQuery(batch.ChildNodes[5],"ItemService","ListID,IsActive,SalesOrPurchase,SalesAndPurchase",false,true);
    FixedQuery(batch.ChildNodes[6],"Account","ListID,IsActive,AccountType,CurrencyRef",false,true);
   }
   if(commercial) {
    for(int i=2;i<batch.ChildNodes.Count;i++) CommercialQuery(batch.ChildNodes[i]);
   }
   if(invoice) {
    FixedQuery(batch.ChildNodes[2],"Preferences","MultiCurrencyPreferences",false);
    int ar=single ? 3 : 4;
    if(!single) FixedQuery(batch.ChildNodes[3],"Currency","ListID,IsActive,CurrencyCode",true);
    FixedQuery(batch.ChildNodes[ar],"Account","ListID,IsActive,AccountType,CurrencyRef",true);
    FixedQuery(batch.ChildNodes[ar+1],"Customer","ListID,IsActive,CurrencyRef",true);
    for(int i=ar+2;i<batch.ChildNodes.Count;i+=2) {
     FixedQuery(batch.ChildNodes[i],"ItemService","ListID,IsActive,SalesOrPurchase,SalesAndPurchase",true);
     FixedQuery(batch.ChildNodes[i+1],"Account","ListID,IsActive,AccountType,CurrencyRef",true);
    }
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

