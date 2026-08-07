const assert = require("node:assert/strict");
const { test } = require("node:test");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function installModule(resolvedPath, exports) {
  require.cache[resolvedPath] = {
    id: resolvedPath,
    filename: resolvedPath,
    loaded: true,
    exports,
  };
}

function instantiatePage(definition) {
  const page = Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
    setDataCalls: [],
  });
  page.setData = (patch) => {
    page.setDataCalls.push(patch);
    Object.assign(page.data, patch);
  };
  return page;
}

test("home read promises cannot setData after the page hides", async () => {
  const operation = deferred();
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, { request: () => operation.promise });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/home/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);

  page.onShow();
  page.onHide();
  const callsAfterHide = page.setDataCalls.length;
  operation.resolve({ data: { items: [] } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(page.setDataCalls.length, callsAfterHide);
  assert.equal(page.data.loading, true);
});

test("home clears cached data when service configuration is removed", () => {
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  let requestCount = 0;
  installModule(requestPath, {
    request: () => {
      requestCount += 1;
      return Promise.resolve({ data: {} });
    },
  });
  installModule(configPath, { configuredApiBase: () => "" });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/home/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.data.greeting = "你好，旧用户";
  page.data.pendingTasks = [{ id: "old-task" }];
  page.data.upcomingEvents = [{ id: "old-event" }];
  page.data.error = "旧错误";

  page.onShow();

  assert.equal(requestCount, 0);
  assert.equal(page.data.configured, false);
  assert.equal(page.data.greeting, "你好");
  assert.deepEqual(page.data.pendingTasks, []);
  assert.deepEqual(page.data.upcomingEvents, []);
  assert.equal(page.data.error, "");
});

test("home clears cached data before a failed refresh", async () => {
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  let requestCount = 0;
  installModule(requestPath, {
    request: () => {
      requestCount += 1;
      return Promise.reject(new Error("offline"));
    },
  });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/home/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.data.greeting = "你好，旧用户";
  page.data.pendingTasks = [{ id: "old-task" }];
  page.data.upcomingEvents = [{ id: "old-event" }];

  page.onShow();

  assert.equal(page.data.loading, true);
  assert.equal(page.data.greeting, "你好");
  assert.deepEqual(page.data.pendingTasks, []);
  assert.deepEqual(page.data.upcomingEvents, []);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requestCount, 3);
  assert.equal(page.data.loading, false);
  assert.deepEqual(page.data.pendingTasks, []);
  assert.deepEqual(page.data.upcomingEvents, []);
  assert.match(page.data.error, /offline/);
});
test("settings locks API mutation while a connection check is pending", async () => {
  const operation = deferred();
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  const authPath = require.resolve("../utils/auth");
  let saveCalls = 0;
  installModule(requestPath, { request: () => operation.promise });
  installModule(configPath, {
    configuredApiBase: () => "https://api.example.edu",
    envVersion: () => "develop",
    saveDevelopmentApiBase: () => {
      saveCalls += 1;
      return "https://changed.example.edu";
    },
  });
  installModule(authPath, {
    clearSession: () => {},
    currentSession: () => null,
    revokeSession: () => Promise.resolve(),
  });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/settings/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.onShow();

  page.checkConnection();
  const inputBefore = page.data.apiInput;
  page.updateApi({ detail: { value: "https://changed.example.edu" } });
  page.saveApi();

  assert.equal(page.data.checking, true);
  assert.equal(page.data.operation, "check");
  assert.equal(page.data.apiInput, inputBefore);
  assert.equal(saveCalls, 0);

  operation.resolve({ data: { status: "ok" } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(page.data.checking, false);
  assert.equal(page.data.operation, "");
  assert.match(page.data.message, /健康检查已通过/);
});

test("settings exposes sign-out separately from connection checking", async () => {
  const operation = deferred();
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  const authPath = require.resolve("../utils/auth");
  installModule(requestPath, {
    request: () => Promise.reject(new Error("connection check must not run")),
  });
  installModule(configPath, {
    configuredApiBase: () => "https://api.example.edu",
    envVersion: () => "develop",
    saveDevelopmentApiBase: (value) => value,
  });
  installModule(authPath, {
    clearSession: () => {},
    currentSession: () => ({ displayName: "测试同学" }),
    revokeSession: () => operation.promise,
  });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/settings/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.onShow();

  page.signOut();

  assert.equal(page.data.checking, true);
  assert.equal(page.data.operation, "signOut");
  operation.resolve();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(page.data.checking, false);
  assert.equal(page.data.operation, "");
  assert.equal(page.data.signedIn, false);
  assert.match(page.data.message, /会话已撤销/);
});
for (const scenario of [
  { name: "notices", page: "notices", collection: "cards" },
  { name: "tasks", page: "tasks", collection: "tasks", editor: true },
  { name: "calendar", page: "calendar", collection: "events", editor: true },
]) {
  test(`${scenario.name} clears stale UI state when service configuration is removed`, () => {
    const requestPath = require.resolve("../utils/request");
    const configPath = require.resolve("../utils/config");
    const authPath = require.resolve("../utils/auth");
    let requestCount = 0;
    installModule(requestPath, {
      request: () => {
        requestCount += 1;
        return Promise.resolve({ data: { items: [] } });
      },
      verifiedRequest: () => {
        requestCount += 1;
        return Promise.resolve({ data: {} });
      },
    });
    installModule(configPath, { configuredApiBase: () => "" });
    installModule(authPath, { currentSession: () => null });

    let definition;
    global.Page = (value) => {
      definition = value;
    };
    const pagePath = require.resolve(`../pages/${scenario.page}/index`);
    delete require.cache[pagePath];
    require(pagePath);
    const page = instantiatePage(definition);
    page.data[scenario.collection] = [{ id: "stale-item" }];
    page.data.error = "旧错误";
    if (scenario.editor) {
      page.data.editorOpen = true;
      page.data.form.title = "未提交的旧内容";
    }

    page.onShow();

    assert.equal(requestCount, 0);
    assert.equal(page.data.configured, false);
    assert.deepEqual(page.data[scenario.collection], []);
    assert.equal(page.data.error, "");
    if (scenario.editor) {
      assert.equal(page.data.editorOpen, false);
      assert.equal(page.data.form.title, "");
    }
  });
}
for (const scenario of [
  { name: "tasks", page: "tasks", collection: "tasks" },
  { name: "calendar", page: "calendar", collection: "events" },
]) {
  test(scenario.name + " preserves same-account drafts and clears cross-scope drafts", () => {
    const requestPath = require.resolve("../utils/request");
    const configPath = require.resolve("../utils/config");
    const authPath = require.resolve("../utils/auth");
    const accountA = "usr_" + "a".repeat(48);
    const accountB = "usr_" + "b".repeat(48);
    let apiBase = "https://api-a.example.edu";
    let accountId = "";
    let logoutGeneration = 0;
    let requestCount = 0;
    installModule(requestPath, {
      request: () => {
        requestCount += 1;
        return new Promise(() => {});
      },
      verifiedRequest: () => Promise.resolve({ data: {} }),
    });
    installModule(configPath, { configuredApiBase: () => apiBase });
    installModule(authPath, {
      currentAccountBoundary: () => ({ accountId, logoutGeneration }),
    });

    let definition;
    global.Page = (value) => {
      definition = value;
    };
    const pagePath = require.resolve("../pages/" + scenario.page + "/index");
    delete require.cache[pagePath];
    require(pagePath);
    const page = instantiatePage(definition);
    page.onShow();
    page.data.editorOpen = true;
    page.data.form.title = "首次登录前填写的草稿";

    accountId = accountA;
    page.onHide();
    page.onShow();

    assert.equal(page.data.editorOpen, true);
    assert.equal(page.data.form.title, "首次登录前填写的草稿");

    page.onHide();
    page.onShow();

    assert.equal(page.data.editorOpen, true);
    assert.equal(page.data.form.title, "首次登录前填写的草稿");

    apiBase = "https://api-b.example.edu";
    page.data[scenario.collection] = [{ id: "service-a-item" }];
    page.onHide();
    page.onShow();

    assert.equal(page.data.editorOpen, false);
    assert.equal(page.data.form.title, "");
    assert.deepEqual(page.data[scenario.collection], []);

    page.data.editorOpen = true;
    page.data.form.title = "旧账号的未提交草稿";
    accountId = accountB;
    page.onHide();
    page.onShow();

    assert.equal(page.data.editorOpen, false);
    assert.equal(page.data.form.title, "");
    assert.deepEqual(page.data[scenario.collection], []);

    page.data.editorOpen = true;
    page.data.form.title = "主动退出前的草稿";
    logoutGeneration += 1;
    page.onHide();
    page.onShow();

    assert.equal(page.data.editorOpen, false);
    assert.equal(page.data.form.title, "");
    assert.deepEqual(page.data[scenario.collection], []);
    assert.equal(requestCount, 6);
  });
}

for (const scenario of [
  { name: "tasks", page: "tasks", collection: "tasks" },
  { name: "calendar", page: "calendar", collection: "events" },
]) {
  for (const failure of ["configuration", "session"]) {
    test(scenario.name + " clears old account UI when " + failure + " storage reads fail", () => {
      const requestPath = require.resolve("../utils/request");
      const configPath = require.resolve("../utils/config");
      const authPath = require.resolve("../utils/auth");
      let requestCount = 0;
      let shouldFail = true;
      installModule(requestPath, {
        request: () => {
          requestCount += 1;
          return Promise.resolve({ data: { items: [] } });
        },
        verifiedRequest: () => Promise.resolve({ data: {} }),
      });
      installModule(configPath, {
        configuredApiBase: () => {
          if (shouldFail && failure === "configuration") {
            throw new Error("configuration storage unavailable");
          }
          return "https://api.example.edu";
        },
      });
      installModule(authPath, {
        currentAccountBoundary: () => {
          if (shouldFail && failure === "session") throw new Error("session storage unavailable");
          return { accountId: "usr_" + "a".repeat(48), logoutGeneration: 0 };
        },
      });

      let definition;
      global.Page = (value) => {
        definition = value;
      };
      const pagePath = require.resolve("../pages/" + scenario.page + "/index");
      delete require.cache[pagePath];
      require(pagePath);
      const page = instantiatePage(definition);
      page.data[scenario.collection] = [{ id: "old-account-item" }];
      page.data.editorOpen = true;
      page.data.form.title = "旧账号草稿";

      assert.doesNotThrow(() => page.onShow());
      assert.equal(requestCount, 0);
      assert.equal(page.data.configured, false);
      assert.equal(page.data.loading, false);
      assert.deepEqual(page.data[scenario.collection], []);
      assert.equal(page.data.editorOpen, false);
      assert.equal(page.data.form.title, "");
      assert.equal(page.data.error, "无法安全读取本机会话，请重试");

      shouldFail = false;
      page.retryLoad();

      assert.equal(requestCount, 1);
      assert.equal(page.data.configured, true);
      assert.equal(page.data.error, "");
    });
  }
}
test("tasks binds a write to the visible account and clears drafts when completion scope is unreadable", async () => {
  const operation = deferred();
  const accountBoundary = { accountId: "usr_" + "a".repeat(48), logoutGeneration: 0 };
  let accountReadFails = false;
  let loadCount = 0;
  let verifiedCall;
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  const authPath = require.resolve("../utils/auth");
  installModule(requestPath, {
    request: () => {
      loadCount += 1;
      return Promise.resolve({ accountBoundary, data: { items: [] } });
    },
    verifiedRequest: (...args) => {
      verifiedCall = args;
      return operation.promise;
    },
  });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });
  installModule(authPath, {
    currentAccountBoundary: () => {
      if (accountReadFails) throw new Error("storage unavailable");
      return accountBoundary;
    },
  });
  global.wx = { showToast: () => {} };

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/tasks/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.onShow();
  await new Promise((resolve) => setImmediate(resolve));
  page.data.editorOpen = true;
  page.data.form.title = "账号 A 的未提交任务";

  page.createTask();
  assert.ok(verifiedCall);
  assert.deepEqual(verifiedCall[3], {
    expectedApiBase: "https://api.example.edu",
    expectedAccountBoundary: accountBoundary,
  });
  accountReadFails = true;
  const boundaryError = new Error("completion boundary unavailable");
  boundaryError.code = "ACCOUNT_BOUNDARY_UNAVAILABLE";
  boundaryError.outcomeUncertain = true;
  operation.reject(boundaryError);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(loadCount, 1);
  assert.equal(page.data.configured, false);
  assert.equal(page.data.busy, false);
  assert.equal(page.data.editorOpen, false);
  assert.equal(page.data.form.title, "");
  assert.deepEqual(page.data.tasks, []);
  assert.equal(page.data.error, "无法安全读取本机会话，请重试");

  accountReadFails = false;
  page.retryLoad();
  assert.equal(loadCount, 2);
});

test("calendar carries one account boundary through conflict check and final write", async () => {
  const operation = deferred();
  const accountBoundary = { accountId: "usr_" + "a".repeat(48), logoutGeneration: 0 };
  let accountReadFails = false;
  let loadCount = 0;
  let conflictOptions;
  let verifiedCall;
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  const authPath = require.resolve("../utils/auth");
  installModule(requestPath, {
    request: (path, options) => {
      if (path === "/api/events/check-conflict") {
        conflictOptions = options;
        return Promise.resolve({ accountBoundary, data: { has_conflict: false } });
      }
      loadCount += 1;
      return Promise.resolve({ accountBoundary, data: { items: [] } });
    },
    verifiedRequest: (...args) => {
      verifiedCall = args;
      return operation.promise;
    },
  });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });
  installModule(authPath, {
    currentAccountBoundary: () => {
      if (accountReadFails) throw new Error("storage unavailable");
      return accountBoundary;
    },
  });
  global.wx = { showToast: () => {} };

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/calendar/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.onLoad();
  page.onShow();
  await new Promise((resolve) => setImmediate(resolve));
  page.data.editorOpen = true;
  page.data.form.title = "账号 A 的未提交日程";
  page.data.form.date = "2026-08-08";

  page.createEvent();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(
    {
      expectedAccountBoundary: conflictOptions.expectedAccountBoundary,
      expectedApiBase: conflictOptions.expectedApiBase,
    },
    {
      expectedAccountBoundary: accountBoundary,
      expectedApiBase: "https://api.example.edu",
    },
  );
  assert.ok(verifiedCall);
  assert.deepEqual(verifiedCall[3], {
    expectedApiBase: "https://api.example.edu",
    expectedAccountBoundary: accountBoundary,
  });

  accountReadFails = true;
  const boundaryError = new Error("completion boundary unavailable");
  boundaryError.code = "ACCOUNT_BOUNDARY_UNAVAILABLE";
  boundaryError.outcomeUncertain = true;
  operation.reject(boundaryError);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(loadCount, 1);
  assert.equal(page.data.configured, false);
  assert.equal(page.data.busy, false);
  assert.equal(page.data.editorOpen, false);
  assert.equal(page.data.form.title, "");
  assert.deepEqual(page.data.events, []);
  assert.equal(page.data.error, "无法安全读取本机会话，请重试");

  accountReadFails = false;
  page.retryLoad();
  assert.equal(loadCount, 2);
});
for (const scenario of [
  {
    name: "home",
    page: "home",
    collection: "pendingTasks",
    requestCountAfterRetry: 3,
  },
  {
    name: "notices",
    page: "notices",
    collection: "cards",
    requestCountAfterRetry: 1,
  },
]) {
  test(scenario.name + " clears old UI when configuration storage fails and retry recovers", () => {
    const requestPath = require.resolve("../utils/request");
    const configPath = require.resolve("../utils/config");
    let shouldFail = true;
    let requestCount = 0;
    installModule(requestPath, {
      request: () => {
        requestCount += 1;
        return new Promise(() => {});
      },
    });
    installModule(configPath, {
      configuredApiBase: () => {
        if (shouldFail) throw new Error("storage unavailable");
        return "https://api.example.edu";
      },
    });

    let definition;
    global.Page = (value) => {
      definition = value;
    };
    const pagePath = require.resolve("../pages/" + scenario.page + "/index");
    delete require.cache[pagePath];
    require(pagePath);
    const page = instantiatePage(definition);
    page.data[scenario.collection] = [{ id: "old-account-item" }];
    if (scenario.name === "home") {
      page.data.greeting = "你好，旧账号";
      page.data.upcomingEvents = [{ id: "old-event" }];
    }

    page.onShow();

    assert.equal(requestCount, 0);
    assert.equal(page.data.configured, false);
    assert.deepEqual(page.data[scenario.collection], []);
    assert.match(page.data.error, /无法安全读取本地配置/);
    if (scenario.name === "home") {
      assert.equal(page.data.greeting, "你好");
      assert.deepEqual(page.data.upcomingEvents, []);
    }

    shouldFail = false;
    page.retryLoad();

    assert.equal(requestCount, scenario.requestCountAfterRetry);
    assert.equal(page.data.configured, true);
    assert.equal(page.data.error, "");
  });
}

test("settings clears old identity when local storage fails and retry recovers", () => {
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  const authPath = require.resolve("../utils/auth");
  let shouldFail = true;
  installModule(requestPath, { request: () => Promise.resolve({ data: {} }) });
  installModule(configPath, {
    envVersion: () => "develop",
    configuredApiBase: () => {
      if (shouldFail) throw new Error("storage unavailable");
      return "https://api.example.edu";
    },
    saveDevelopmentApiBase: () => "https://api.example.edu",
  });
  installModule(authPath, {
    clearSession: () => true,
    currentSession: () => ({ displayName: "新账号" }),
    revokeSession: () => Promise.resolve(),
  });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/settings/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.data.apiInput = "https://old.example.edu";
  page.data.signedIn = true;
  page.data.displayName = "旧账号";

  page.onShow();

  assert.equal(page.data.configured, false);
  assert.equal(page.data.apiInput, "");
  assert.equal(page.data.signedIn, false);
  assert.equal(page.data.displayName, "");
  assert.equal(page.data.localStateReadFailed, true);
  assert.match(page.data.error, /无法安全读取本地配置/);

  shouldFail = false;
  page.retryLocalState();

  assert.equal(page.data.configured, true);
  assert.equal(page.data.signedIn, true);
  assert.equal(page.data.displayName, "新账号");
  assert.equal(page.data.localStateReadFailed, false);
  assert.equal(page.data.error, "");
});

test("settings does not report API save success when old credentials cannot be cleared", () => {
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  const authPath = require.resolve("../utils/auth");
  const apiBase = "https://api.example.edu";
  let clearCalls = 0;
  let savedValue = "";
  installModule(requestPath, { request: () => Promise.resolve({ data: {} }) });
  installModule(configPath, {
    envVersion: () => "develop",
    configuredApiBase: () => apiBase,
    saveDevelopmentApiBase: (value) => {
      savedValue = value;
      return value;
    },
  });
  installModule(authPath, {
    clearSession: () => {
      clearCalls += 1;
      return false;
    },
    currentSession: () => ({ displayName: "旧账号" }),
    revokeSession: () => Promise.resolve(),
  });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/settings/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.onShow();
  assert.equal(page.data.apiInput, apiBase);
  assert.equal(page.data.signedIn, true);
  assert.equal(page.data.displayName, "旧账号");

  page.saveApi();

  assert.equal(savedValue, apiBase);
  assert.equal(clearCalls, 1);
  assert.equal(page.data.apiInput, apiBase);
  assert.equal(page.data.configured, true);
  assert.equal(page.data.signedIn, true);
  assert.equal(page.data.displayName, "旧账号");
  assert.equal(page.data.message, "");
  assert.match(page.data.error, /无法从本机删除旧登录凭证/);
});

test("settings treats an unknown environment as a failed local-state read", () => {
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  const authPath = require.resolve("../utils/auth");
  let environment = "unknown";
  let configReads = 0;
  let sessionReads = 0;
  installModule(requestPath, { request: () => Promise.resolve({ data: {} }) });
  installModule(configPath, {
    envVersion: () => environment,
    configuredApiBase: () => {
      configReads += 1;
      return "https://api.example.edu";
    },
    saveDevelopmentApiBase: () => "https://api.example.edu",
  });
  installModule(authPath, {
    clearSession: () => true,
    currentSession: () => {
      sessionReads += 1;
      return { displayName: "新账号" };
    },
    revokeSession: () => Promise.resolve(),
  });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/settings/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.data.apiInput = "https://old.example.edu";
  page.data.configured = true;
  page.data.signedIn = true;
  page.data.displayName = "旧账号";

  page.onShow();

  assert.equal(configReads, 0);
  assert.equal(sessionReads, 0);
  assert.equal(page.data.environment, "");
  assert.equal(page.data.release, true);
  assert.equal(page.data.apiInput, "");
  assert.equal(page.data.configured, false);
  assert.equal(page.data.signedIn, false);
  assert.equal(page.data.displayName, "");
  assert.equal(page.data.localStateReadFailed, true);
  assert.equal(page.data.error, "无法安全读取本地配置，请重试");

  environment = "develop";
  page.retryLocalState();

  assert.equal(configReads, 1);
  assert.equal(sessionReads, 1);
  assert.equal(page.data.environment, "develop");
  assert.equal(page.data.configured, true);
  assert.equal(page.data.signedIn, true);
  assert.equal(page.data.displayName, "新账号");
  assert.equal(page.data.localStateReadFailed, false);
  assert.equal(page.data.error, "");
});

test("settings health success fails closed when refreshing the local session throws", async () => {
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  const authPath = require.resolve("../utils/auth");
  let sessionReads = 0;
  installModule(requestPath, { request: () => Promise.resolve({ data: { status: "ok" } }) });
  installModule(configPath, {
    envVersion: () => "develop",
    configuredApiBase: () => "https://api.example.edu",
    saveDevelopmentApiBase: () => "https://api.example.edu",
  });
  installModule(authPath, {
    clearSession: () => true,
    currentSession: () => {
      sessionReads += 1;
      if (sessionReads > 1) throw new Error("session storage unavailable");
      return { displayName: "旧账号" };
    },
    revokeSession: () => Promise.resolve(),
  });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/settings/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.onShow();
  assert.equal(page.data.signedIn, true);
  assert.equal(page.data.displayName, "旧账号");

  page.checkConnection();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(sessionReads, 2);
  assert.equal(page.data.checking, false);
  assert.equal(page.data.operation, "");
  assert.equal(page.data.configured, false);
  assert.equal(page.data.apiInput, "");
  assert.equal(page.data.signedIn, false);
  assert.equal(page.data.displayName, "");
  assert.equal(page.data.message, "");
  assert.equal(page.data.localStateReadFailed, true);
  assert.equal(page.data.error, "无法安全读取本地配置，请重试");
});

test("privacy disables deletion when configuration storage fails and retry recovers", () => {
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  let shouldFail = true;
  installModule(requestPath, { request: () => Promise.resolve({ data: {} }) });
  installModule(configPath, {
    configuredApiBase: () => {
      if (shouldFail) throw new Error("storage unavailable");
      return "https://api.example.edu";
    },
  });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/privacy/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.data.message = "旧账号操作成功";
  page.data.error = "旧账号错误";

  page.onShow();

  assert.equal(page.data.configured, false);
  assert.equal(page.data.message, "");
  assert.equal(page.data.error, "");
  assert.match(page.data.localStateError, /无法安全读取本地配置/);

  shouldFail = false;
  page.retryLocalState();

  assert.equal(page.data.configured, true);
  assert.equal(page.data.localStateError, "");
});
test("voice clears completed or active transcripts across account boundaries", () => {
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  const authPath = require.resolve("../utils/auth");
  const voiceSessionPath = require.resolve("../utils/voice-session");
  let apiBase = "https://api.example.edu";
  let accountId = "usr_" + "a".repeat(48);
  let logoutGeneration = 0;
  let storageFailure = false;
  let observer = null;
  let resetCount = 0;
  let discardCount = 0;
  let state = {
    phase: "done",
    statusText: "识别完成",
    transcript: "上一会话的转写",
    error: "",
  };
  const publish = () => {
    if (observer) observer.onState(Object.assign({}, state));
  };
  const controller = {
    attach: (value) => {
      observer = value;
      publish();
    },
    detach: () => {
      observer = null;
    },
    snapshot: () => Object.assign({}, state),
    reset: () => {
      resetCount += 1;
      state = {
        phase: "idle",
        statusText: "点击开始，说出你的任务、日程或校园问题",
        transcript: "",
        error: "",
      };
      publish();
      return true;
    },
    discard: (message) => {
      discardCount += 1;
      state = {
        phase: "stopping",
        statusText: "正在安全停止录音…",
        transcript: "",
        error: message,
      };
      publish();
    },
  };
  installModule(requestPath, { request: () => Promise.resolve({ data: {} }) });
  installModule(configPath, {
    configuredApiBase: () => {
      if (storageFailure) throw new Error("storage unavailable");
      return apiBase;
    },
    websocketBase: () => "wss://api.example.edu",
  });
  installModule(authPath, {
    currentAccountBoundary: () => ({ accountId, logoutGeneration }),
  });
  installModule(voiceSessionPath, {
    createVoiceSessionController: () => controller,
  });
  global.wx = {
    getRecorderManager: () => ({}),
  };

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/voice/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);
  page.onLoad();
  assert.equal(page.data.transcript, "上一会话的转写");

  logoutGeneration += 1;
  page.onShow();
  assert.equal(resetCount, 1);
  assert.equal(page.data.transcript, "");

  state = {
    phase: "recording",
    statusText: "正在聆听…",
    transcript: "录音中的敏感转写",
    error: "",
  };
  publish();
  storageFailure = true;
  page.onShow();

  assert.equal(discardCount, 1);
  assert.equal(page.data.configured, false);
  assert.equal(page.data.transcript, "");
  assert.equal(page.data.error, "无法安全读取本地配置，请重试");
});
test("privacy challenge completion cannot open a modal or setData after hide", async () => {
  const operation = deferred();
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, { request: () => operation.promise });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  let modalCalls = 0;
  global.wx = {
    showModal: () => {
      modalCalls += 1;
    },
  };
  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/privacy/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);

  page.onShow();
  page.issueDeletionChallenge();
  page.onHide();
  const callsAfterHide = page.setDataCalls.length;
  operation.resolve({ data: { id: "challenge-id", challenge: "challenge-value" } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(modalCalls, 0);
  assert.equal(page.setDataCalls.length, callsAfterHide);
  assert.equal(page._deletionPending, false);
});
test("privacy final modal always releases pending state across hide and show", async () => {
  const operation = deferred();
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, { request: () => operation.promise });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  const modalOptions = [];
  global.wx = { showModal: (options) => modalOptions.push(options) };
  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/privacy/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);

  page.onShow();
  page.issueDeletionChallenge();
  operation.resolve({ data: { id: "challenge-id", challenge: "challenge-value" } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(modalOptions.length, 1);

  page.onHide();
  page.onShow();
  assert.equal(page.data.deleting, true);
  modalOptions[0].success({ confirm: false });

  assert.equal(page._deletionPending, false);
  assert.equal(page.data.deleting, false);
  assert.match(page.data.error, /页面状态已变化/);
});

test("privacy deletion terminal result reaches the new visible generation", async () => {
  const challenge = deferred();
  const confirmation = deferred();
  let requestCount = 0;
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, {
    request: () => {
      requestCount += 1;
      return requestCount === 1 ? challenge.promise : confirmation.promise;
    },
  });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  const modalOptions = [];
  global.wx = { showModal: (options) => modalOptions.push(options) };
  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/privacy/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);

  page.onShow();
  page.issueDeletionChallenge();
  challenge.resolve({ data: { id: "challenge-id", challenge: "challenge-value" } });
  await new Promise((resolve) => setImmediate(resolve));
  modalOptions[0].success({ confirm: true });
  assert.equal(requestCount, 2);

  page.onHide();
  page.onShow();
  confirmation.resolve({ data: { verified: true } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(page._deletionPending, false);
  assert.equal(page.data.deleting, false);
  assert.match(page.data.message, /已删除并由服务端核验/);
});
test("privacy deletion result survives old Page unload and reaches a new instance", async () => {
  const challenge = deferred();
  const confirmation = deferred();
  let requestCount = 0;
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, {
    request: () => {
      requestCount += 1;
      return requestCount === 1 ? challenge.promise : confirmation.promise;
    },
  });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  const modalOptions = [];
  global.wx = { showModal: (options) => modalOptions.push(options) };
  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/privacy/index");
  delete require.cache[pagePath];
  require(pagePath);
  const oldPage = instantiatePage(definition);

  oldPage.onShow();
  oldPage.issueDeletionChallenge();
  challenge.resolve({ data: { id: "challenge-id", challenge: "challenge-value" } });
  await new Promise((resolve) => setImmediate(resolve));
  modalOptions[0].success({ confirm: true });
  oldPage.onUnload();

  const newPage = instantiatePage(definition);
  newPage.onShow();
  assert.equal(newPage.data.deleting, true);
  confirmation.resolve({ data: { verified: true } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(newPage.data.deleting, false);
  assert.match(newPage.data.message, /已删除并由服务端核验/);
});
