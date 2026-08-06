const fs = require("node:fs");
const moduleApi = require("node:module");
const path = require("node:path");
const vm = require("node:vm");

const projectRoot = path.resolve(__dirname, "..");
const buildMode = process.argv.includes("--build");
const failures = [];

function relative(filePath) {
  return path.relative(projectRoot, filePath).replaceAll(path.sep, "/");
}

function walk(directory) {
  return fs
    .readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      const resolved = path.join(directory, entry.name);
      return entry.isDirectory() ? walk(resolved) : [resolved];
    })
    .sort();
}

function readUtf8(filePath) {
  const buffer = fs.readFileSync(filePath);
  if (buffer.length >= 3 && buffer[0] === 0xef && buffer[1] === 0xbb && buffer[2] === 0xbf) {
    throw new Error("UTF-8 BOM is not allowed");
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
}

function check(filePath, operation) {
  try {
    operation();
  } catch (error) {
    failures.push(relative(filePath) + ": " + (error && error.message ? error.message : error));
  }
}

function checkJavaScript(filePath, source) {
  new vm.Script(source, { filename: relative(filePath) });
  const localRequire = moduleApi.createRequire(filePath);
  const imports = source.matchAll(/\brequire\(\s*["']([^"']+)["']\s*\)/g);
  for (const match of imports) {
    if (!match[1].startsWith(".")) continue;
    localRequire.resolve(match[1]);
  }
}

function checkWxml(filePath, source) {
  const stack = [];
  const withoutComments = source.replace(/<!--[\s\S]*?-->/g, "");
  const tags = withoutComments.matchAll(/<\s*(\/?)\s*([A-Za-z][\w-]*)\b([^>]*)>/g);
  for (const match of tags) {
    const closing = Boolean(match[1]);
    const name = match[2];
    const tail = match[3];
    if (!closing && /\/\s*$/.test(tail)) continue;
    if (!closing) {
      stack.push(name);
      continue;
    }
    const opened = stack.pop();
    if (opened !== name) {
      throw new Error(
        "mismatched closing tag </" + name + ">; expected </" + (opened || "none") + ">",
      );
    }
  }
  if (stack.length) throw new Error("unclosed tag <" + stack.at(-1) + ">");
}

function checkWxss(filePath, source) {
  const withoutComments = source.replace(/\/\*[\s\S]*?\*\//g, "");
  let depth = 0;
  for (const character of withoutComments) {
    if (character === "{") depth += 1;
    if (character === "}") depth -= 1;
    if (depth < 0) throw new Error("unexpected closing brace");
  }
  if (depth !== 0) throw new Error("unclosed style block");
}

const files = walk(projectRoot).filter(
  (filePath) => !filePath.includes(path.join(projectRoot, "node_modules") + path.sep),
);
const jsonFiles = files.filter((filePath) => path.extname(filePath) === ".json");
const jsFiles = files.filter((filePath) => path.extname(filePath) === ".js");
const wxmlFiles = files.filter((filePath) => path.extname(filePath) === ".wxml");
const wxssFiles = files.filter((filePath) => path.extname(filePath) === ".wxss");

for (const filePath of jsonFiles) {
  check(filePath, () => JSON.parse(readUtf8(filePath)));
}
for (const filePath of jsFiles) {
  check(filePath, () => checkJavaScript(filePath, readUtf8(filePath)));
}
for (const filePath of wxmlFiles) {
  check(filePath, () => checkWxml(filePath, readUtf8(filePath)));
}
for (const filePath of wxssFiles) {
  check(filePath, () => checkWxss(filePath, readUtf8(filePath)));
}

const appJsonPath = path.join(projectRoot, "app.json");
const projectJsonPath = path.join(projectRoot, "project.config.json");
check(appJsonPath, () => {
  const app = JSON.parse(readUtf8(appJsonPath));
  if (!Array.isArray(app.pages) || app.pages.length === 0)
    throw new Error("pages must not be empty");
  for (const page of app.pages) {
    for (const extension of [".js", ".json", ".wxml", ".wxss"]) {
      const pageFile = path.join(projectRoot, page + extension);
      if (!fs.existsSync(pageFile)) throw new Error(page + extension + " is missing");
    }
  }
  for (const item of (app.tabBar && app.tabBar.list) || []) {
    if (!app.pages.includes(item.pagePath)) {
      throw new Error("tabBar page is not declared in pages: " + item.pagePath);
    }
  }
  const recordDescription = app.permission && app.permission["scope.record"];
  if (
    !recordDescription ||
    typeof recordDescription.desc !== "string" ||
    !recordDescription.desc.trim()
  ) {
    throw new Error("permission.scope.record.desc must not be empty");
  }
});

check(projectJsonPath, () => {
  const project = JSON.parse(readUtf8(projectJsonPath));
  const runtimeConfig = require(path.join(projectRoot, "config.js"));
  if (project.compileType !== "miniprogram") throw new Error("compileType must be miniprogram");
  if (project.miniprogramRoot !== "./") throw new Error("miniprogramRoot must be ./");
  if (!/^wx[a-f0-9]{16}$/.test(project.appid || "")) throw new Error("appid is invalid");
  if (runtimeConfig.appId !== project.appid)
    throw new Error("config.js appId does not match project appid");
  const ignored = new Set(
    ((project.packOptions && project.packOptions.ignore) || []).map(
      (item) => item.type + ":" + item.value,
    ),
  );
  for (const required of [
    "file:README.md",
    "file:package.json",
    "folder:scripts",
    "folder:tests",
  ]) {
    if (!ignored.has(required)) throw new Error("release pack must ignore " + required);
  }
  const allowedConfigKeys = ["apiBaseUrl", "appId", "appName", "privacyVersion"];
  if (Object.keys(runtimeConfig).sort().join(",") !== allowedConfigKeys.sort().join(",")) {
    throw new Error("config.js contains an unexpected key");
  }
});

if (failures.length) {
  for (const failure of failures) console.error("ERROR " + failure);
  process.exitCode = 1;
} else {
  console.log(
    "Static mini-program gate passed: " +
      jsFiles.length +
      " JavaScript, " +
      jsonFiles.length +
      " JSON, " +
      wxmlFiles.length +
      " WXML, " +
      wxssFiles.length +
      " WXSS files.",
  );
  if (buildMode) {
    console.log(
      "This command does not compile or package a WeChat release; WeChat DevTools compile/preview remains mandatory.",
    );
    if (!require(path.join(projectRoot, "config.js")).apiBaseUrl) {
      console.log(
        "Release blocker: config.js apiBaseUrl is empty; do not upload until the registered HTTPS/WSS origin is configured and verified.",
      );
    }
  }
}
