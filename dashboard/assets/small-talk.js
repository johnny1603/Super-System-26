/* small-talk.js — is this chat message only small talk? (docs/CHAT-SPEED.md §4b)

   "היי", "מה קורה?", "תודה", "אחלה" and the like in he / en / fr / ar / ru are
   not work: the client dashboard keeps the plain typing indicator for them and
   never shows the progress line or a time estimate.

   DISPLAY ONLY. The server decides the real route by its own rules
   (core/chat_route.small_talk). A phrase missing here just behaves as before;
   a real request must never match — the WHOLE message has to be made of these
   words ("היי, תעלה לי את הסרטון" is not small talk).
   tests/test_small_talk.py runs this file and the server's rules on the same phrases. */
(function () {
  'use strict';
  var WORDS = 'הי+|שלום|אהלן|הלו|בוקר טוב|ערב טוב|לילה טוב|מה קורה|מה הולך|מה נשמע|מה שלומך|מה העניינים|'
    + 'תודה+( רבה)?|תנקס|אחלה|סבבה|מעולה|יופי|מצוין|נהדר|אוקיי?|אוקי|מגניב|מושלם|כל הכבוד|ביי|'
    + 'ג\'וני|ניסים|אלינור|יואב|אורי|מאיה|ליאור|'
    + 'hi+|hey+|hello|good (morning|afternoon|evening|night)|how are you( doing)?|how\'?s it going|what\'?s up|sup|'
    + 'thanks?( you)?( so much| a lot)?|thx|ok(ay)?|great|cool|perfect|nice|awesome|bye|'
    + 'johnny|nissim|elinor|yoav|uri|maya|lior|'
    + 'bonjour|bonsoir|salut|coucou|(comment )?[çc]a va|merci( beaucoup)?|super|parfait|d\'accord|'
    + 'مرحبا|[أا]هلا|السلام عليكم|صباح الخير|مساء الخير|كيف حالك|كيفك|شو ال[أا]خبار|شكرا( جزيلا)?|تمام|ممتاز|'
    + 'привет|здравствуйте|добрый день|как дела|как ты|спасибо( большое)?|отлично|супер|класс|хорошо|пока';
  var NAMES = /^(ג'וני|ניסים|אלינור|יואב|אורי|מאיה|ליאור|johnny|nissim|elinor|yoav|uri|maya|lior)$/i;
  var FILL = '[\\s,!.?؟،…~\\-\\u{FE0F}🙏😊👍❤🙂😄😁👋💪🔥]';
  var RX;
  try {
    RX = new RegExp('^' + FILL + '*(?:(?:' + WORDS + ')' + FILL + '+)*(?:' + WORDS + ')' + FILL + '*$', 'iu');
  } catch (e) { RX = null; }                           // an old browser: nothing is small talk

  window.uallakSmallTalk = function (text) {
    var t = String(text == null ? '' : text).trim();
    if (!RX || !t || t.length > 70 || t.indexOf('\n') !== -1 || NAMES.test(t)) return false;
    return RX.test(t);
  };
})();
