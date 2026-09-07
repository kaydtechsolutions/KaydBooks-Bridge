"use strict";
// All language/core files are local. Document bytes never select code, URLs or models.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

async function main() {
  if (process.versions.node.split(".")[0] !== "22")
    throw Error("unqualified Node major version");
  const watchdog = setTimeout(() => process.exit(2), 40000);
  watchdog.unref();
  const [modules, input, output] = process.argv.slice(2);
  if (![modules, input, output].every((p) => p && path.isAbsolute(p)))
    throw Error("absolute paths required");
  const packageDir = path.join(modules, "tesseract.js");
  const version = JSON.parse(
    fs.readFileSync(path.join(packageDir, "package.json")),
  ).version;
  if (version !== "7.0.0") throw Error("unqualified OCR version");
  const language = path.join(
    modules,
    "@tesseract.js-data",
    "eng",
    "4.0.0_best_int",
  );
  const model = fs.readFileSync(path.join(language, "eng.traineddata.gz"));
  const { createWorker } = require(packageDir);
  const worker = await createWorker("eng", 1, {
    langPath: language,
    corePath: path.join(modules, "tesseract.js-core"),
    workerPath: path.join(
      packageDir,
      "src",
      "worker-script",
      "node",
      "index.js",
    ),
    cacheMethod: "none",
    gzip: true,
    logger: () => {},
  });
  try {
    await worker.setParameters({
      preserve_interword_spaces: "1",
      user_defined_dpi: "150",
    });
    const { data } = await worker.recognize(
      input,
      {},
      { text: true, blocks: true },
    );
    const lines = [];
    for (const block of data.blocks || [])
      for (const paragraph of block.paragraphs || []) {
        for (const line of paragraph.lines || [])
          lines.push({
            text: line.text,
            confidence: line.confidence,
            bbox: line.bbox,
          });
      }
    fs.writeFileSync(
      output,
      JSON.stringify({
        text: data.text,
        confidence: data.confidence,
        lines,
        engine: "tesseract.js",
        node: process.versions.node,
        version,
        language: "eng",
        model_sha256: crypto.createHash("sha256").update(model).digest("hex"),
      }),
      { flag: "wx" },
    );
  } finally {
    await worker.terminate();
    clearTimeout(watchdog);
  }
}
main().catch(() => {
  process.stderr.write("local OCR failed\n");
  process.exitCode = 1;
});
