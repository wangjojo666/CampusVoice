const assert = require("node:assert/strict");
const { test } = require("node:test");

const {
  hidePage,
  isPageCurrent,
  pageGeneration,
  showPage,
  unloadPage,
} = require("../utils/page-lifecycle");

test("hide invalidates callbacks from the visible page generation", () => {
  const page = {};
  const generation = showPage(page);
  assert.equal(isPageCurrent(page, generation), true);

  hidePage(page);
  assert.equal(isPageCurrent(page, generation), false);
  assert.notEqual(pageGeneration(page), generation);
});

test("a new show generation cannot be polluted by callbacks from a prior show", () => {
  const page = {};
  const first = showPage(page);
  hidePage(page);
  const second = showPage(page);

  assert.equal(isPageCurrent(page, first), false);
  assert.equal(isPageCurrent(page, second), true);
});

test("unload permanently invalidates the current generation until a real show", () => {
  const page = {};
  const generation = showPage(page);
  unloadPage(page);

  assert.equal(isPageCurrent(page, generation), false);
  assert.equal(page.__pageAlive, false);
  assert.equal(page.__pageVisible, false);
});
