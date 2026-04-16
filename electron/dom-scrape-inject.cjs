'use strict';

/**
 * 生成在页面上下文中执行的表达式字符串（由 webContents.executeJavaScript 运行）。
 * selectors 由主进程 JSON.stringify 后嵌入，禁止拼接未转义的用户脚本。
 * @param {string} selectorsJsonLiteral — JSON.stringify(selectorsArray) 的结果
 */
function buildDomScrapeExpression(selectorsJsonLiteral) {
  return `(function(){
    var selectors = ${selectorsJsonLiteral};
    var out = {
      scrapedAt: new Date().toISOString(),
      href: location.href,
      title: document.title,
      byDataAiwriter: [],
      byDataMetric: [],
      bySelector: {}
    };
    try {
      document.querySelectorAll('[data-aiwriter-metric]').forEach(function (el) {
        out.byDataAiwriter.push({
          key: (el.getAttribute('data-aiwriter-metric') || '').trim(),
          text: (el.textContent || '').trim().substring(0, 2000)
        });
      });
      document.querySelectorAll('[data-metric]').forEach(function (el) {
        out.byDataMetric.push({
          key: (el.getAttribute('data-metric') || '').trim(),
          text: (el.textContent || '').trim().substring(0, 2000)
        });
      });
      if (Array.isArray(selectors)) {
        selectors.forEach(function (row) {
          if (!row || !row.key || !row.selector) return;
          try {
            var el = document.querySelector(row.selector);
            out.bySelector[row.key] = el
              ? (el.textContent || '').trim().substring(0, 2000)
              : null;
          } catch (err) {
            out.bySelector[row.key] = { error: String((err && err.message) || err) };
          }
        });
      }
    } catch (e) {
      out._scrapeError = String((e && e.message) || e);
    }
    return out;
  })()`;
}

module.exports = { buildDomScrapeExpression };
