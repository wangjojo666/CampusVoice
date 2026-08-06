import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { cp, mkdir, mkdtemp, rm, stat } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(scriptDirectory, "..");
const standaloneSource = path.join(webRoot, ".next", "standalone");
const staticSource = path.join(webRoot, ".next", "static");
const publicSource = path.join(webRoot, "public");
const standaloneEntry = path.join("apps", "web", "server.js");
const requestTimeoutMs = 10_000;
const startupTimeoutMs = 30_000;
const shutdownTimeoutMs = 5_000;

const expectedManifest = Object.freeze({
  name: "声程 CampusVoice",
  short_name: "声程",
  start_url: "/",
  scope: "/",
  display: "standalone",
  theme_color: "#0e7f6d",
  background_color: "#f7faf9",
});

async function assertDirectory(directory) {
  const info = await stat(directory);
  assert.equal(info.isDirectory(), true, directory + " must be a directory");
}

async function reservePort() {
  const server = net.createServer();
  server.unref();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert(address && typeof address === "object");
  const port = address.port;
  await new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
  return port;
}

function waitForStandaloneReady(child, getLogs) {
  return new Promise((resolve, reject) => {
    let readyOutput = "";
    let settled = false;

    const finish = (error) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      child.stdout.off("data", inspect);
      child.stderr.off("data", inspect);
      child.off("error", onError);
      child.off("exit", onExit);
      if (error) {
        reject(error);
      } else {
        resolve();
      }
    };

    const inspect = (chunk) => {
      readyOutput = (readyOutput + chunk.toString("utf8")).slice(-8_192);
      if (/Ready in/i.test(readyOutput)) {
        finish();
      }
    };

    const onError = (error) => {
      finish(error);
    };

    const onExit = (code, signal) => {
      finish(
        new Error(
          "Standalone server exited before readiness (code=" +
            String(code) +
            ", signal=" +
            String(signal) +
            ").\n" +
            getLogs(),
        ),
      );
    };

    const timer = setTimeout(() => {
      finish(
        new Error(
          "Standalone server did not become ready within " +
            String(startupTimeoutMs) +
            "ms.\n" +
            getLogs(),
        ),
      );
    }, startupTimeoutMs);

    child.stdout.on("data", inspect);
    child.stderr.on("data", inspect);
    child.once("error", onError);
    child.once("exit", onExit);
  });
}

function waitForExitWithin(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve(true);
  }

  return new Promise((resolve) => {
    const onExit = () => {
      clearTimeout(timer);
      resolve(true);
    };
    const timer = setTimeout(() => {
      child.off("exit", onExit);
      resolve(false);
    }, timeoutMs);
    child.once("exit", onExit);
  });
}

async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) {
    return;
  }

  child.kill("SIGTERM");
  if (await waitForExitWithin(child, shutdownTimeoutMs)) {
    return;
  }

  child.kill("SIGKILL");
  const exited = await waitForExitWithin(child, shutdownTimeoutMs);
  assert.equal(exited, true, "Standalone server did not stop after SIGKILL");
}

async function fetchWithTimeout(url, accept) {
  return fetch(url, {
    headers: accept ? { Accept: accept } : undefined,
    redirect: "error",
    signal: AbortSignal.timeout(requestTimeoutMs),
  });
}

function contentType(response) {
  return response.headers.get("content-type") || "";
}

function manifestPurpose(icon) {
  return String(icon.purpose || "any")
    .split(/\s+/)
    .filter(Boolean);
}

function assertManifestIcon(manifest, expected) {
  const match = manifest.icons.find(
    (icon) =>
      icon.src === expected.src &&
      String(icon.sizes || "")
        .split(/\s+/)
        .includes(expected.sizes) &&
      icon.type === "image/png" &&
      manifestPurpose(icon).includes(expected.purpose),
  );
  assert(
    match,
    "Manifest must advertise " +
      expected.src +
      " as " +
      expected.sizes +
      " image/png with purpose " +
      expected.purpose,
  );
}

function parseTags(html, tagName) {
  const tags = [];
  const tagPattern = new RegExp("<" + tagName + "\\b[^>]*>", "gi");
  for (const tagMatch of html.matchAll(tagPattern)) {
    const attributes = {};
    const attributePattern = /([:\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
    for (const attributeMatch of tagMatch[0].matchAll(attributePattern)) {
      const value = attributeMatch[2] === undefined ? attributeMatch[3] : attributeMatch[2];
      attributes[attributeMatch[1].toLowerCase()] = value;
    }
    tags.push(attributes);
  }
  return tags;
}

function relIncludes(attributes, value) {
  return String(attributes.rel || "")
    .toLowerCase()
    .split(/\s+/)
    .includes(value);
}

function assertRootMetadata(html) {
  const links = parseTags(html, "link");
  const metadata = parseTags(html, "meta");

  assert(
    links.some(
      (attributes) =>
        relIncludes(attributes, "manifest") && attributes.href === "/manifest.webmanifest",
    ),
    "Root HTML must link /manifest.webmanifest",
  );
  assert(
    links.some(
      (attributes) =>
        relIncludes(attributes, "apple-touch-icon") && attributes.href === "/pwa/icon-192",
    ),
    "Root HTML must advertise /pwa/icon-192 as the Apple touch icon",
  );

  const namedMetadata = (name) =>
    metadata.find(
      (attributes) => String(attributes.name || "").toLowerCase() === name.toLowerCase(),
    );

  assert.equal(
    namedMetadata("theme-color")?.content,
    expectedManifest.theme_color,
    "Root HTML theme-color must match the manifest",
  );

  const viewport = String(namedMetadata("viewport")?.content || "")
    .toLowerCase()
    .replace(/\s+/g, "");
  assert(viewport.includes("width=device-width"));
  assert(viewport.includes("initial-scale=1"));
  assert(viewport.includes("viewport-fit=cover"));

  assert.equal(
    String(namedMetadata("apple-mobile-web-app-capable")?.content || "").toLowerCase(),
    "yes",
  );
  assert.equal(namedMetadata("apple-mobile-web-app-title")?.content, expectedManifest.short_name);
}

function collectStaticAssets(html) {
  const assets = new Set();
  for (const tagName of ["link", "script"]) {
    for (const attributes of parseTags(html, tagName)) {
      for (const key of ["href", "src"]) {
        const value = attributes[key];
        if (value && value.startsWith("/_next/static/")) {
          assets.add(value.replaceAll("&amp;", "&"));
        }
      }
    }
  }
  assert(assets.size > 0, "Root HTML must reference at least one Next static asset");
  return [...assets];
}

function pngCrc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function assertCompletePng(bytes, expected) {
  const pngSignature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  assert(bytes.length >= 45, expected.src + " must contain a complete PNG");
  assert(
    bytes.subarray(0, pngSignature.length).equals(pngSignature),
    expected.src + " must have a valid PNG signature",
  );

  let chunkIndex = 0;
  let offset = pngSignature.length;
  let sawImageData = false;
  let sawImageEnd = false;

  while (offset < bytes.length) {
    assert(
      bytes.length - offset >= 12,
      expected.src + " must not contain a truncated PNG chunk header",
    );
    const dataLength = bytes.readUInt32BE(offset);
    const typeOffset = offset + 4;
    const dataOffset = offset + 8;
    const dataEnd = dataOffset + dataLength;
    const chunkEnd = dataEnd + 4;
    const chunkType = bytes.subarray(typeOffset, dataOffset).toString("ascii");
    assert(
      chunkEnd <= bytes.length,
      expected.src + " must not contain a truncated " + chunkType + " chunk",
    );
    assert.equal(
      bytes.readUInt32BE(dataEnd),
      pngCrc32(bytes.subarray(typeOffset, dataEnd)),
      expected.src + " " + chunkType + " CRC must be valid",
    );

    if (chunkIndex === 0) {
      assert.equal(chunkType, "IHDR", expected.src + " must start with IHDR");
      assert.equal(dataLength, 13, expected.src + " IHDR must be 13 bytes");
      assert.equal(
        bytes.readUInt32BE(dataOffset),
        expected.width,
        expected.src + " width must match the manifest",
      );
      assert.equal(
        bytes.readUInt32BE(dataOffset + 4),
        expected.height,
        expected.src + " height must match the manifest",
      );
    }

    if (chunkType === "IDAT") {
      sawImageData = true;
    }
    if (chunkType === "IEND") {
      assert.equal(dataLength, 0, expected.src + " IEND must be empty");
      assert.equal(chunkEnd, bytes.length, expected.src + " must not contain bytes after IEND");
      sawImageEnd = true;
      break;
    }

    offset = chunkEnd;
    chunkIndex += 1;
  }

  assert(sawImageData, expected.src + " must contain image data");
  assert(sawImageEnd, expected.src + " must end with IEND");
}

async function verifyPng(baseUrl, expected) {
  const response = await fetchWithTimeout(new URL(expected.src, baseUrl), "image/png");
  assert.equal(response.status, 200, expected.src + " must return HTTP 200");
  assert.match(
    contentType(response),
    /^image\/png(?:;|$)/i,
    expected.src + " must return image/png",
  );

  const bytes = Buffer.from(await response.arrayBuffer());
  assertCompletePng(bytes, expected);
}

async function verifyResponseBody(baseUrl, assetPath) {
  const response = await fetchWithTimeout(new URL(assetPath, baseUrl));
  assert.equal(response.status, 200, assetPath + " must return HTTP 200");
  const bytes = Buffer.from(await response.arrayBuffer());
  assert(bytes.length > 0, assetPath + " must not be empty");
}

async function main() {
  await Promise.all([
    assertDirectory(standaloneSource),
    assertDirectory(staticSource),
    assertDirectory(publicSource),
  ]);

  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "campusvoice-standalone-pwa-"));
  const runtimeRoot = path.join(temporaryRoot, "runtime");
  let child;

  try {
    await cp(standaloneSource, runtimeRoot, {
      recursive: true,
      dereference: process.platform === "win32",
    });

    const runtimeWebRoot = path.join(runtimeRoot, "apps", "web");
    await mkdir(path.join(runtimeWebRoot, ".next"), { recursive: true });
    await cp(staticSource, path.join(runtimeWebRoot, ".next", "static"), {
      recursive: true,
    });
    await cp(publicSource, path.join(runtimeWebRoot, "public"), {
      recursive: true,
    });

    const entrypoint = path.join(runtimeRoot, standaloneEntry);
    const entrypointInfo = await stat(entrypoint);
    assert.equal(
      entrypointInfo.isFile(),
      true,
      standaloneEntry + " must be the standalone entrypoint",
    );

    const port = await reservePort();
    const baseUrl = new URL("http://127.0.0.1:" + String(port));
    let processLogs = "";
    const recordLogs = (chunk) => {
      processLogs = (processLogs + chunk.toString("utf8")).slice(-32_768);
    };

    const childEnvironment = {
      ...process.env,
      HOSTNAME: "127.0.0.1",
      NEXT_TELEMETRY_DISABLED: "1",
      NODE_ENV: "production",
      PORT: String(port),
    };
    if (process.platform === "win32") {
      // Dereferencing junctions moves pnpm packages out of their virtual-store parents.
      childEnvironment.NODE_PATH = [
        path.join(runtimeRoot, "node_modules", ".pnpm", "node_modules"),
        process.env.NODE_PATH,
      ]
        .filter(Boolean)
        .join(path.delimiter);
    }

    child = spawn(process.execPath, [entrypoint], {
      cwd: runtimeRoot,
      env: childEnvironment,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    child.stdout.on("data", recordLogs);
    child.stderr.on("data", recordLogs);

    await waitForStandaloneReady(child, () => processLogs);

    const manifestResponse = await fetchWithTimeout(
      new URL("/manifest.webmanifest", baseUrl),
      "application/manifest+json",
    );
    assert.equal(manifestResponse.status, 200, "/manifest.webmanifest must return HTTP 200");
    assert.match(
      contentType(manifestResponse),
      /^application\/manifest\+json(?:;|$)/i,
      "/manifest.webmanifest must return application/manifest+json",
    );
    const manifest = await manifestResponse.json();
    for (const [key, value] of Object.entries(expectedManifest)) {
      assert.deepEqual(
        manifest[key],
        value,
        "Manifest field " + key + " must match the release contract",
      );
    }
    assert(Array.isArray(manifest.icons), "Manifest icons must be an array");

    const icons = [
      {
        src: "/pwa/icon-192",
        sizes: "192x192",
        purpose: "any",
        width: 192,
        height: 192,
      },
      {
        src: "/pwa/icon-512",
        sizes: "512x512",
        purpose: "any",
        width: 512,
        height: 512,
      },
      {
        src: "/pwa/maskable-512",
        sizes: "512x512",
        purpose: "maskable",
        width: 512,
        height: 512,
      },
    ];

    for (const icon of icons) {
      assertManifestIcon(manifest, icon);
      await verifyPng(baseUrl, icon);
    }

    const rootResponse = await fetchWithTimeout(new URL("/", baseUrl), "text/html");
    assert.equal(rootResponse.status, 200, "/ must return HTTP 200");
    assert.match(contentType(rootResponse), /^text\/html(?:;|$)/i);
    const html = await rootResponse.text();
    assertRootMetadata(html);

    const staticAssets = collectStaticAssets(html);
    for (const assetPath of staticAssets) {
      await verifyResponseBody(baseUrl, assetPath);
    }
    await verifyResponseBody(baseUrl, "/audio-processor.js");

    console.log(
      "Standalone PWA smoke passed: entry=" +
        standaloneEntry.replaceAll("\\", "/") +
        ", manifest=1, icons=" +
        String(icons.length) +
        ", static_assets=" +
        String(staticAssets.length) +
        ", public_assets=1",
    );
  } finally {
    try {
      await stopChild(child);
    } finally {
      const relativeTemporaryRoot = path.relative(os.tmpdir(), temporaryRoot);
      assert(
        relativeTemporaryRoot &&
          relativeTemporaryRoot !== ".." &&
          !relativeTemporaryRoot.startsWith(".." + path.sep) &&
          !path.isAbsolute(relativeTemporaryRoot),
        "Refusing to remove a path outside the OS temporary directory",
      );
      await rm(temporaryRoot, { force: true, recursive: true, maxRetries: 3 });
    }
  }
}

await main();
