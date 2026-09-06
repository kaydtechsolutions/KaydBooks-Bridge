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
