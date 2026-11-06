/* Chat timestamps (docs/CHAT_TIMESTAMPS.md): every stored time is UTC
 * (client_communications.created_at, agent_actions.created_at/decided_at —
 * timestamptz); this file is the ONE place they become the client's local
 * time. Zones are IANA names handled by Intl.DateTimeFormat, so daylight
 * saving moves with the zone's own rules — never a fixed offset.
 *
 * Zone order: the client's setting (clients.timezone, default
 * Asia/Jerusalem) → the browser's zone → Asia/Jerusalem.
 *
 * UMD: the dashboard loads it as window.uallakChatTime; the tests require()
 * it under Node (tests/test_chat_timestamps.py). No DOM, no dependencies.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.uallakChatTime = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var DEFAULT_TZ = 'Asia/Jerusalem';
  var formatters = {};

  // An IANA NAME only: newer engines also accept "+03:00", a fixed offset
  // that would show the wrong time for half the year — refused here
  function validZone(tz) {
    if (!tz || typeof tz !== 'string' || tz.length > 64) return false;
    if (!/^(UTC|[A-Za-z][A-Za-z0-9_+\-]*(\/[A-Za-z0-9_+\-]+){1,2})$/.test(tz)) return false;
    try { new Intl.DateTimeFormat('en-US', { timeZone: tz }); return true; } catch (e) { return false; }
  }

  function browserZone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { return ''; }
  }

  function resolveZone(clientTz, browserTz) {
    if (validZone(clientTz)) return clientTz;
    var b = browserTz === undefined ? browserZone() : browserTz;
    return validZone(b) ? b : DEFAULT_TZ;
  }

  // Supabase sends "2026-11-07T08:00:00.123456+00:00"; a value without an
  // offset is UTC by our storage rule. More than 3 fraction digits is cut
  // (not every engine parses microseconds).
  function toDate(value) {
    if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
    if (typeof value === 'number') { var n = new Date(value); return isNaN(n.getTime()) ? null : n; }
    if (typeof value !== 'string' || !value) return null;
    var s = value.trim().replace(' ', 'T').replace(/(\.\d{3})\d+/, '$1');
    if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(s)) s += 'Z';
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  function parts(value, tz) {
    var d = toDate(value);
    if (!d) return null;
    var zone = validZone(tz) ? tz : DEFAULT_TZ;
    var f = formatters[zone] || (formatters[zone] = new Intl.DateTimeFormat('en-US', {
      timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
    }));
    var out = {};
    f.formatToParts(d).forEach(function (p) { if (p.type !== 'literal') out[p.type] = p.value; });
    if (out.hour === '24') out.hour = '00';   // older engines with hour12:false
    return out;
  }

  function time(value, tz) {           // HH:MM, 24h
    var p = parts(value, tz);
    return p ? p.hour + ':' + p.minute : '';
  }

  function date(value, tz) {           // dd/mm/yyyy
    var p = parts(value, tz);
    return p ? p.day + '/' + p.month + '/' + p.year : '';
  }

  function full(value, tz) {           // dd/mm/yyyy HH:MM:SS — the hover / long-press text
    var p = parts(value, tz);
    return p ? p.day + '/' + p.month + '/' + p.year + ' ' + p.hour + ':' + p.minute + ':' + p.second : '';
  }

  function stamp(value, tz) {          // dd/mm/yyyy HH:MM — the conversation download
    var p = parts(value, tz);
    return p ? p.day + '/' + p.month + '/' + p.year + ' ' + p.hour + ':' + p.minute : '';
  }

  function dayKey(value, tz) {         // yyyy-mm-dd in the zone: what "the same day" means
    var p = parts(value, tz);
    return p ? p.year + '-' + p.month + '-' + p.day : '';
  }

  // The calendar day before `key` — date arithmetic on the calendar, not
  // "now minus 24 hours", which is wrong across a DST change
  function previousDay(key) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key || '');
    if (!m) return '';
    var d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] - 1));
    return d.getUTCFullYear() + '-' + ('0' + (d.getUTCMonth() + 1)).slice(-2) + '-' + ('0' + d.getUTCDate()).slice(-2);
  }

  // The separator between days: "היום" / "אתמול" / dd/mm/yyyy (labels from i18n)
  function dayLabel(value, tz, now, labels) {
    var key = dayKey(value, tz);
    if (!key) return '';
    var today = dayKey(now === undefined ? new Date() : now, tz);
    var l = labels || { today: 'היום', yesterday: 'אתמול' };
    if (key === today) return l.today;
    if (key === previousDay(today)) return l.yesterday;
    return date(value, tz);
  }

  // [{at, ...}] in display order → the indexes that start a new day (where a
  // separator goes). Items without a readable time never start a day.
  function separatorIndexes(items, tz) {
    var out = [], last = '';
    (items || []).forEach(function (item, i) {
      var key = dayKey(item && item.at, tz);
      if (key && key !== last) { out.push(i); last = key; }
    });
    return out;
  }

  return {
    DEFAULT_TZ: DEFAULT_TZ, validZone: validZone, browserZone: browserZone, resolveZone: resolveZone,
    toDate: toDate, time: time, date: date, full: full, stamp: stamp, dayKey: dayKey,
    previousDay: previousDay, dayLabel: dayLabel, separatorIndexes: separatorIndexes
  };
}));
