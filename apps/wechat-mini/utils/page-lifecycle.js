function showPage(page) {
  page.__pageAlive = true;
  page.__pageVisible = true;
  page.__pageGeneration = (page.__pageGeneration || 0) + 1;
  return page.__pageGeneration;
}

function hidePage(page) {
  page.__pageVisible = false;
  page.__pageGeneration = (page.__pageGeneration || 0) + 1;
}

function unloadPage(page) {
  page.__pageAlive = false;
  page.__pageVisible = false;
  page.__pageGeneration = (page.__pageGeneration || 0) + 1;
}

function pageGeneration(page) {
  return page.__pageGeneration || 0;
}

function isPageCurrent(page, generation) {
  return Boolean(page.__pageAlive && page.__pageVisible && page.__pageGeneration === generation);
}

module.exports = { hidePage, isPageCurrent, pageGeneration, showPage, unloadPage };
