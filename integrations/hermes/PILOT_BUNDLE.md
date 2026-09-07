# KaydBooks Bridge development candidate

This bundle contains the Windows wheel, the separate Linux Hermes plugin and setup
guides. It is not a production release or a one-click installer. No company files,
credentials, mappings, WhatsApp sessions or private qualification records are included.
The source commit and SHA-256 of every included file are in `manifest.json`.

1. On Windows, extract to a new directory and create a Python 3.12 environment:

   ```powershell
   py -3.12 -m venv C:\KaydBooks-Runtime
   $wheel = (Get-ChildItem -LiteralPath .\windows -Filter *.whl).FullName
   & C:\KaydBooks-Runtime\Scripts\python.exe -m pip install "${wheel}[server,hermes,intake]"
   & C:\KaydBooks-Runtime\Scripts\kaydbooks-bridge.exe capabilities
   ```

   Dependency installation requires network access. This wheel does not contain
   QuickBooks Desktop, Web Connector, Python, or Hermes itself.
2. Follow [company setup and Web Connector registration](docs/INSTALL_HERMES_DATA_ENTRY.md).
   Keep configuration outside this bundle. An existing installation needs a verified
   backup and an idle upgrade, not a second dispatcher for the same company.
3. On Linux, copy `hermes/kaydbooks` into the **actual operator gateway profile's**
   `plugins/kaydbooks` directory. Follow [channel setup](docs/HERMES_CHANNEL.md) to
   configure private SSH launchers, MCP filtering, confirmation and the worker.
4. Finish the [acceptance walkthrough](docs/HERMES_DATA_ENTRY_PILOT.md). Existing
   sample qualification does not authorize production company writes.

Build this bundle from a checkout with a separately built wheel:

```text
python tools/build_pilot_bundle.py --wheel dist/kaydbooks_bridge-0.1.0.dev1-py3-none-any.whl --output dist/KB-0.1.0-dev-pilot.zip
```

Use a new output filename for each candidate. Publishing remains a separate action.
