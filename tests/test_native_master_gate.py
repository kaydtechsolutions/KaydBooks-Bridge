"""Exercise the actual native field allowlist without opening QuickBooks."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell native compiler")
def test_native_master_fields_reject_extra_or_unbounded_requests(tmp_path):
    source = (Path(__file__).parents[1] / "src/kaydbooks_bridge/direct_sdk.ps1").read_text()
    method = source[
        source.index(" static void FixedQuery(") : source.index(" public static void Run(")
    ]
    script = tmp_path / "native-gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\n"
        + "using System; public static class Gate {\n"
        + method
        + """
 public static bool Allowed(string xml,bool exact,bool preview) {
  try {
   var doc=new System.Xml.XmlDocument(); doc.LoadXml(xml);
   FixedQuery(doc.DocumentElement,"Customer","ListID,IsActive,CurrencyRef",exact,preview);
   return true;
  } catch { return false; }
 }
 public static bool CommercialAllowed(string xml) {
  try {
   var doc=new System.Xml.XmlDocument(); doc.LoadXml(xml);
   CommercialQuery(doc.DocumentElement); return true;
  } catch { return false; }
 }
}
'@
$fields='<IncludeRetElement>ListID</IncludeRetElement><IncludeRetElement>IsActive</IncludeRetElement><IncludeRetElement>CurrencyRef</IncludeRetElement>'
$exact='<CustomerQueryRq requestID="13"><ListID>synthetic-id</ListID>'+$fields+'</CustomerQueryRq>'
$preview='<CustomerQueryRq requestID="13"><MaxReturned>20</MaxReturned><ActiveStatus>ActiveOnly</ActiveStatus>'+$fields+'</CustomerQueryRq>'
if (-not [Gate]::Allowed($exact,$true,$false)) {throw 'valid exact rejected'}
if (-not [Gate]::Allowed($preview,$false,$true)) {throw 'valid preview rejected'}
foreach($bad in @($exact.Replace('</ListID>','</ListID><ListID>other</ListID>'),$exact.Replace('synthetic-id','bad id'),$exact.Replace('<ListID>synthetic-id','<ListID x="1">synthetic-id'),$exact.Replace('</CustomerQueryRq>','<IncludeRetElement>CreditCardInfo</IncludeRetElement></CustomerQueryRq>'),$exact.Replace('CustomerQueryRq','CustomerAddRq'))) {
 if ([Gate]::Allowed($bad,$true,$false)) {throw 'unsafe exact accepted'}
}
if ([Gate]::Allowed($preview.Replace('>20<','>21<'),$false,$true)) {throw 'unbounded preview accepted'}
$tax='<SalesTaxCodeQueryRq requestID="139"><ListID>tax-code</ListID><IncludeRetElement>ListID</IncludeRetElement><IncludeRetElement>IsActive</IncludeRetElement><IncludeRetElement>IsTaxable</IncludeRetElement></SalesTaxCodeQueryRq>'
if (-not [Gate]::CommercialAllowed($tax)) {throw 'tax projection rejected'}
foreach($bad in @($tax.Replace('QueryRq','AddRq'),$tax.Replace('</ListID>','</ListID><ListID>other</ListID>'),$tax.Replace('IsTaxable','CreditCardInfo'),$tax.Replace('<ListID>tax-code</ListID>','<MaxReturned>21</MaxReturned><ActiveStatus>ActiveOnly</ActiveStatus>'))) {
 if ([Gate]::CommercialAllowed($bad)) {throw 'unsafe commercial query accepted'}
}
Write-Output 'Native master gate passed'
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-File",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert "Native master gate passed" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell native compiler")
def test_native_invoice_receipt_gate(tmp_path):
    from kaydbooks_bridge.invoice_receipt import append_lookup
    from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService

    source = (Path(__file__).parents[1] / "src/kaydbooks_bridge/direct_sdk.ps1").read_text()
    start = source.index(
        '   if(batch.ChildNodes.Count==3 && batch.ChildNodes[2].Name=="InvoiceQueryRq")'
    )
    end = source.index("   if(batch.ChildNodes.Count==7 && !single)", start)
    gate = source[start:end]
    request = append_lookup(
        DurableQBWCDiscoveryService._discovery_request("1234", "17.0"), "1234", "saved-id"
    )
    request_file = tmp_path / "request.xml"
    request_file.write_text(request)
    script = tmp_path / "receipt-gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\n"
        + "using System; public static class ReceiptGate { public static bool Allowed(string xml) { try {\n"
        + "var doc=new System.Xml.XmlDocument(); doc.LoadXml(xml); var batch=doc.DocumentElement.FirstChild;\n"
        + gate
        + "return true; } catch { return false; } } }\n'@\n"
        + "$query=[System.IO.File]::ReadAllText($args[0])\n"
        + """
if (-not [ReceiptGate]::Allowed($query)) {throw 'valid receipt rejected'}
foreach($bad in @(
 $query.Replace('InvoiceQueryRq','InvoiceAddRq'),
 $query.Replace('<TxnID>saved-id</TxnID>','<RefNumber>saved-id</RefNumber>'),
 $query.Replace('</TxnID>','</TxnID><TxnID>second</TxnID>'),
 $query.Replace('<TxnID>','<TxnID x="1">'),
 $query.Replace('saved-id','bad id'),
 $query.Replace('<IncludeLineItems>true','<IncludeLineItems>false'),
 $query.Replace('<IncludeLinkedTxns>true','<IncludeLinkedTxns>false'),
 $query.Replace('<IncludeRetElement>Subtotal</IncludeRetElement>',''),
 $query.Replace('</InvoiceQueryRq>','<IncludeRetElement>CreditCardInfo</IncludeRetElement></InvoiceQueryRq>')
)) {if ([ReceiptGate]::Allowed($bad)) {throw 'unsafe receipt query accepted'}}
Write-Output 'Native receipt gate passed'
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-File",
            str(script),
            str(request_file),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert "Native receipt gate passed" in result.stdout
