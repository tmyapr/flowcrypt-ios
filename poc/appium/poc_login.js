/**
 * FlowCrypt iOS - IMAP credential logging PoC driver (Appium / XCUITest).
 *
 * Target: commit 60d3370 of FlowCrypt/flowcrypt-ios (last build that still
 * renders the "Other email provider" button, removed in 842645b / #1858-#1866).
 *
 * What it does:
 *   1. attaches to the already-installed app (does not reinstall anything)
 *   2. taps "Other email provider"
 *   3. fills email / password, points IMAP+SMTP at the local plaintext mock
 *      servers (Security type = none -> ConnectionType.none)
 *   4. taps Connect and walks the key-setup screens as far as it can
 *
 * Every step writes a screenshot + the page source into OUT_DIR so the run is
 * self-documenting even when a selector drifts.
 *
 * Usage:
 *   node poc_login.js --udid <SIM_UDID> --out /tmp/poc/out \
 *        --email poc@mockmail.test --password 'PocPassw0rd!'
 */
const fs = require('fs');
const path = require('path');
const { remote } = require('webdriverio');

const arg = (name, def) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : def;
};

const UDID = arg('udid');
const OUT = arg('out', '/tmp/poc/out');
const EMAIL = arg('email', 'poc@mockmail.test');
const PASSWORD = arg('password', 'PocPassw0rd!');
const IMAP_HOST = arg('imap-host', '127.0.0.1');
const IMAP_PORT = arg('imap-port', '1143');
const SMTP_HOST = arg('smtp-host', '127.0.0.1');
const SMTP_PORT = arg('smtp-port', '2525');
const BUNDLE_ID = arg('bundle-id', 'com.flowcrypt.as.ios.consumer');
const APPIUM_PORT = parseInt(arg('appium-port', '4723'), 10);

if (!UDID) {
  console.error('missing --udid');
  process.exit(2);
}
fs.mkdirSync(OUT, { recursive: true });

const steps = [];
let stepNo = 0;

const record = (name, ok, extra = {}) => {
  steps.push({ step: ++stepNo, name, ok, ...extra, at: new Date().toISOString() });
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${name} ${extra.error ? '- ' + extra.error : ''}`);
};

const shot = async (driver, label) => {
  const file = path.join(OUT, `shot-${String(stepNo).padStart(2, '0')}-${label}.png`);
  try {
    await driver.saveScreenshot(file);
  } catch (e) {
    /* non fatal */
  }
};

const dumpSource = async (driver, label) => {
  try {
    const src = await driver.getPageSource();
    fs.writeFileSync(path.join(OUT, `source-${String(stepNo).padStart(2, '0')}-${label}.xml`), src);
  } catch (e) {
    /* non fatal */
  }
};

/** Resolve a selector to a displayed element, scrolling it into view. */
const find = async (driver, selector, timeout = 20000) => {
  const el = await driver.$(selector);
  await el.waitForExist({ timeout });
  try {
    await driver.execute('mobile: scroll', { elementId: el.elementId, toVisible: true, direction: 'down' });
  } catch (e) {
    /* scroll unsupported for that element type */
  }
  return el;
};

const typeInto = async (driver, selector, text) => {
  const el = await find(driver, selector);
  await el.click();
  await driver.pause(400);
  try {
    await el.setValue(text);
  } catch (e) {
    await driver.keys(text.split(''));
  }
  await driver.pause(400);
  try {
    await driver.hideKeyboard();
  } catch (e) {
    /* keyboard may already be gone */
  }
};

const clickIt = async (driver, selector) => {
  const el = await find(driver, selector);
  await el.click();
  await driver.pause(600);
};

const step = async (driver, name, fn) => {
  try {
    await fn();
    await shot(driver, name.replace(/[^a-z0-9]+/gi, '-').toLowerCase());
    record(name, true);
    return true;
  } catch (e) {
    await shot(driver, 'fail-' + name.replace(/[^a-z0-9]+/gi, '-').toLowerCase());
    await dumpSource(driver, 'fail');
    record(name, false, { error: String(e && e.message ? e.message : e).slice(0, 400) });
    return false;
  }
};

(async () => {
  const driver = await remote({
    hostname: '127.0.0.1',
    port: APPIUM_PORT,
    path: '/',
    logLevel: 'error',
    connectionRetryTimeout: 240000,
    capabilities: {
      platformName: 'iOS',
      'appium:automationName': 'XCUITest',
      'appium:udid': UDID,
      'appium:bundleId': BUNDLE_ID,
      // attach to the process we already launched with `simctl --console-pty`
      // so that its stdout (the leaked credential lines) keeps flowing
      'appium:forceAppLaunch': false,
      'appium:shouldTerminateApp': false,
      'appium:noReset': true,
      'appium:wdaLaunchTimeout': 240000,
      'appium:newCommandTimeout': 300,
    },
  });

  try {
    await step(driver, 'attach-to-app', async () => {
      await driver.pause(3000);
    });

    const tappedOther = await step(driver, 'tap-other-email-provider', async () => {
      await clickIt(driver, '~aid-sign-in-other-btn');
    });
    if (!tappedOther) {
      // fall back to a fresh launch in case the app was still on a splash state
      await step(driver, 'relaunch-and-tap-other', async () => {
        await driver.execute('mobile: launchApp', { bundleId: BUNDLE_ID });
        await driver.pause(4000);
        await clickIt(driver, '~aid-sign-in-other-btn');
      });
    }

    await step(driver, 'fill-email', async () => typeInto(driver, '~aid-email-textfield', EMAIL));
    await step(driver, 'fill-password', async () => typeInto(driver, '~aid-password-textfield', PASSWORD));

    await step(driver, 'fill-imap-server', async () =>
      typeInto(driver, '-ios predicate string:placeholderValue == "IMAP server"', IMAP_HOST));
    await step(driver, 'fill-imap-port', async () => typeInto(driver, '~IMAP port', IMAP_PORT));
    await step(driver, 'set-imap-security-none', async () => clickIt(driver, '~IMAP type'));

    await step(driver, 'fill-smtp-server', async () =>
      typeInto(driver, '-ios predicate string:placeholderValue == "SMTP server"', SMTP_HOST));
    await step(driver, 'fill-smtp-port', async () => typeInto(driver, '~SMTP port', SMTP_PORT));
    await step(driver, 'set-smtp-security-none', async () => clickIt(driver, '~SMTP type'));

    // Connect -> IMAP LOGIN + SMTP AUTH hit the mock servers (and the app log)
    await step(driver, 'tap-connect', async () => clickIt(driver, '~aid-connect-button'));
    await step(driver, 'wait-connection-result', async () => driver.pause(15000));

    // best-effort key setup so the account is persisted for the upgrade leg
    await step(driver, 'setup-create-new-key', async () => clickIt(driver, '~aid-create-new-key-button'));
    await step(driver, 'setup-passphrase', async () => {
      const secure = await driver.$('-ios class chain:**/XCUIElementTypeSecureTextField');
      await secure.waitForExist({ timeout: 30000 });
      await secure.click();
      await secure.setValue(PASSWORD);
      await driver.pause(500);
      try { await driver.hideKeyboard(); } catch (e) { /* ignore */ }
    });
    await step(driver, 'setup-passphrase-confirm', async () => {
      const confirm = await driver.$('~textField');
      if (await confirm.isExisting()) {
        await confirm.click();
        await confirm.setValue(PASSWORD);
        try { await driver.hideKeyboard(); } catch (e) { /* ignore */ }
      }
    });
    await step(driver, 'setup-submit', async () => clickIt(driver, '~aid-set-pass-phrase-btn'));
    await step(driver, 'wait-setup-finish', async () => driver.pause(30000));

    await dumpSource(driver, 'final');
    await shot(driver, 'final');
  } finally {
    try { await driver.deleteSession(); } catch (e) { /* ignore */ }
    fs.writeFileSync(path.join(OUT, 'appium-steps.json'), JSON.stringify(steps, null, 2));
    const failed = steps.filter((s) => !s.ok).length;
    console.log(`\n${steps.length - failed}/${steps.length} steps ok -> ${OUT}`);
    process.exit(failed === 0 ? 0 : 1);
  }
})().catch((e) => {
  console.error('fatal:', e);
  fs.writeFileSync(path.join(OUT, 'appium-steps.json'), JSON.stringify(steps, null, 2));
  process.exit(3);
});
