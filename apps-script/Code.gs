/**
 * clockout sheet writer — for the `gsheet_webapp` destination.
 *
 * 1. In YOUR sheet: Extensions → Apps Script, paste this file, set TOKEN (same value as the
 *    token_env secret in ~/.config/clockout/secrets.env), save.
 * 2. Deploy → New deployment → Web app (Execute as: Me, Who has access: Anyone). Put the URL
 *    in secrets.env under the url_env name. The sheet itself can stay Restricted.
 *
 * Works with any column layout: clockout sends the headers it expects; if the tab's header row
 * differs, nothing is written. Rows are only appended after the last row. The only deletion is
 * replace=true, which removes rows whose person column == person AND date column is one of the
 * dates being written.
 */
const TOKEN = 'CHANGE_ME_TO_A_LONG_RANDOM_STRING';

function doPost(e) {
  let req;
  try { req = JSON.parse(e.postData.contents); } catch (err) { return out({ error: 'bad json' }); }
  if (req.token !== TOKEN) return out({ error: 'bad token' });
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sh = getTab(ss, req.tab || 'Timesheet', req.headers);
    const url = ss.getUrl() + '#gid=' + sh.getSheetId();
    if (req.action !== 'append') return out({ error: 'unknown action' });

    const clashes = findRows(ss, sh, req);
    if (clashes.length && !req.replace) return out({ error: 'rows already exist for these dates', rows: clashes });
    clashes.sort((a, b) => b - a).forEach(r => sh.deleteRow(r));

    const n = req.rows.length, ncol = req.headers.length, start = sh.getLastRow() + 1;
    if (!n) return out({ appended: 'nothing', replaced: clashes.length, sheet_url: url });
    const values = req.rows.map(r => r.map((v, i) => {
      const k = req.columns[i] || { kind: 'text' };
      if (k.kind === 'date' && v) return parseDate(v);
      if (k.kind === 'number') return (v === '' || v == null) ? '' : Number(v);
      return v == null ? '' : String(v);
    }));
    req.columns.forEach((k, i) => {   // text columns literal, so "- bullet" is never a formula
      if (k.kind === 'text') sh.getRange(start, i + 1, n, 1).setNumberFormat('@');
    });
    sh.getRange(start, 1, n, ncol).setValues(values).setWrap(true).setVerticalAlignment('middle');
    req.columns.forEach((k, i) => {
      if (k.kind === 'date') sh.getRange(start, i + 1, n, 1).setNumberFormat(k.pattern);
      if (k.kind === 'number') sh.getRange(start, i + 1, n, 1).setNumberFormat('0.##');
    });
    return out({ appended: `${sh.getName()}!R${start}:R${start + n - 1}`, replaced: clashes.length, sheet_url: url });
  } catch (err) {
    return out({ error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function getTab(ss, name, headers) {
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
    sh.setFrozenRows(1);
    return sh;
  }
  const head = sh.getRange(1, 1, 1, headers.length).getValues()[0].map(v => String(v).trim());
  if (head.every(h => h === '')) {
    sh.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
  } else if (head.join('|') !== headers.join('|')) {
    throw new Error(`header row is [${head}] but clockout expects [${headers}]; update the project layout`);
  }
  return sh;
}

function findRows(ss, sh, req) {
  if (req.date_col == null) return [];
  const tz = ss.getSpreadsheetTimeZone(), want = new Set(req.dates || []), rows = [];
  const data = sh.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    const d = data[i][req.date_col];
    const iso = d instanceof Date ? Utilities.formatDate(d, tz, 'yyyy-MM-dd') : String(d);
    const samePerson = req.person_col == null || String(data[i][req.person_col]).trim() === req.person;
    if (samePerson && want.has(iso)) rows.push(i + 1);
  }
  return rows;
}

function parseDate(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  return new Date(y, m - 1, d);
}

function out(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
