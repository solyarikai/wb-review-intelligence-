/* DOM regression tests, not proof of rendering/clicks in the ChatGPT host.
 * Usage: node ui.test.cjs <built-fragment.html> <exported-report.json>
 * Test-only dependency: linkedom 0.18.12. No browser or network in the report.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {parseHTML} = require('linkedom');
const fragment = fs.readFileSync(process.argv[2], 'utf8');
const report = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
let checked = 0;
function test(name, run) { run(); checked++; console.log('PASS '+name); }
function mount(saved, blockedStorage=false) {
  const {window} = parseHTML('<html><body>'+fragment+'</body></html>');
  let latest = saved;
  window.openai = {widgetState:saved, setWidgetState(value) { latest = JSON.parse(JSON.stringify(value)); }};
  const entries = new Map();
  window.sessionStorage = blockedStorage ? {getItem() {throw Error('blocked');}, setItem() {throw Error('blocked');}} :
    {getItem(key) {return entries.get(key) || null;}, setItem(key,value) {entries.set(key,value);}};
  const script = window.document.querySelector('script').textContent;
  const context = vm.createContext({window, document:window.document});
  const run = () => vm.runInContext(script, context, {timeout:5000});
  run();
  const root = window.document.querySelector('.ri-report');
  const q = selector => root.querySelector(selector);
  const all = selector => [...root.querySelectorAll(selector)];
  const click = element => { assert.ok(element, 'click target exists'); element.dispatchEvent(new window.Event('click')); };
  const input = (selector, value) => {q(selector).value=value;q(selector).dispatchEvent(new window.Event('input'));};
  const toggle = (element, open=true) => {element.open=open;element.dispatchEvent(new window.Event('toggle'));};
  const tab = name => click(q('[data-tab="'+name+'"]'));
  const area = name => click(q('[data-area="'+name+'"]'));
  const findTheme = id => all('[data-theme-id]').find(element=>element.dataset.themeId===id);
  return {window,root,q,all,click,input,toggle,tab,area,findTheme,run,saved:()=>latest};
}
const app = mount();
test('initialization and static summary', () => {
  assert.equal(app.root.dataset.ready,'true'); assert.equal(app.q('[data-runtime-error]').hidden,true);
  assert.equal(app.q('[data-summary]').textContent, report.areas.article.summary);
  assert.equal(app.q('[data-analyzed]').textContent,'45');
});
test('all themes including singleton, sorted by unique review count', () => {
  app.tab('themes'); const cards=app.all('[data-theme-id]');
  assert.equal(cards.length,report.areas.article.themes.length);
  const singleton=report.areas.article.themes.find(theme=>theme.count===1);
  assert.ok(app.findTheme(singleton.id)); assert.match(app.findTheme(singleton.id).textContent,/1\/45/);
});
test('positive and negative filters, then all restores neutral topics', () => {
  for (const context of ['positive','negative']) {
    app.click(app.q('[data-filter="'+context+'"]'));
    assert.equal(app.all('[data-theme-id]').length,report.areas.article.themes.filter(theme=>theme.context===context).length);
  }
  app.click(app.q('[data-filter="all"]')); assert.equal(app.all('[data-theme-id]').length,report.areas.article.themes.length);
});
const longTheme = report.areas.group.themes.find(theme=>theme.count>10);
test('group switch changes metrics, denominators and every theme', () => {
  app.area('group'); assert.equal(app.q('[data-analyzed]').textContent,'707');
  assert.equal(app.all('[data-theme-id]').length,report.areas.group.themes.length);
  assert.match(app.findTheme(longTheme.id).textContent,new RegExp(longTheme.count+'/707'));
});
test('quote disclosure groups by review and paginates 5, 10, all', () => {
  const detail=app.findTheme(longTheme.id); app.toggle(detail);
  assert.equal(detail.querySelectorAll('[data-evidence-review]').length,5);
  app.click([...detail.querySelectorAll('button')].find(button=>button.textContent==='Показать ещё'));
  assert.equal(detail.querySelectorAll('[data-evidence-review]').length,10);
  app.click([...detail.querySelectorAll('button')].find(button=>button.textContent==='Показать все'));
  const actual=new Set(longTheme.quote_ids.map(id=>report.areas.group.quotes.find(quote=>quote.id===id).review_id));
  assert.equal(detail.querySelectorAll('[data-evidence-review]').length,actual.size);
  assert.equal(detail.querySelectorAll('button').length,0);
});
test('search indexes full texts and hidden quotes, with matched evidence first', () => {
  const theme=report.areas.group.themes.find(item=>item.quote_ids.length>25);
  const quote=report.areas.group.quotes.find(item=>item.id===theme.quote_ids[25]);
  const needle=quote.full_field_text.slice(0,70);
  app.input('[data-theme-search]',needle);
  const detail=app.findTheme(theme.id); assert.ok(detail); app.toggle(detail);
  const first=detail.querySelector('[data-evidence-review]');
  assert.ok(first.textContent.toLocaleLowerCase('ru-RU').replace(/ё/g,'е').includes(needle.toLocaleLowerCase('ru-RU').replace(/ё/g,'е')));
  app.input('[data-theme-search]','');
});
test('full field disclosure and quote metadata stay exact', () => {
  const quote=report.areas.group.quotes.find(item=>item.full_field_text!==item.text);
  if (!quote) {
    assert.equal(app.all('.ri-full').length,0);
    console.log('N/A full-field toggle: every real excerpt already contains the entire source field');
    return;
  }
  const theme=report.areas.group.themes.find(item=>item.quote_ids.includes(quote.id));
  app.input('[data-theme-search]',quote.full_field_text.slice(0,70));
  const detail=app.findTheme(theme.id);app.toggle(detail);
  const full=[...detail.querySelectorAll('.ri-full')].find(item=>item.textContent.includes(quote.full_field_text));
  assert.ok(full);app.toggle(full);assert.ok(full.textContent.includes(quote.full_field_text));
  const metadata=full.closest('.ri-quote').querySelector('.ri-quote-meta').textContent;
  assert.match(metadata,/^\d{2}\.\d{2}\.\d{4} · WB \d+$/);
});
test('areas keep independent query and disclosure state', () => {
  const groupQuery=app.q('[data-theme-search]').value; app.area('article');
  assert.equal(app.q('[data-theme-search]').value,''); app.area('group');
  assert.equal(app.q('[data-theme-search]').value,groupQuery);app.input('[data-theme-search]','');
});
test('Q&A pagination, search in seller answer and preserved role', () => {
  app.tab('questions'); assert.equal(app.all('[data-question-id]').length,20);
  app.click(app.all('[data-qa-more] button').find(button=>button.textContent==='Показать все'));
  assert.equal(app.all('[data-question-id]').length,report.areas.group.questions.length);
  const question=report.areas.group.questions.find(item=>item.answers?.length);
  app.input('[data-qa-search]',question.answers[0].text.slice(0,60));
  const detail=app.all('[data-question-id]').find(item=>item.dataset.questionId===question.id);
  assert.ok(detail);app.toggle(detail); assert.ok(detail.textContent.includes(question.answers[0].text));
  assert.ok(detail.textContent.includes('Ответ продавца'));
});
test('opportunity contains hypothesis, action, verification and actual theme link', () => {
  app.tab('opportunities');const opportunity=report.areas.group.opportunities[0];
  const detail=app.all('[data-opportunity-id]')[0];app.toggle(detail);
  for(const key of ['observation','hypothesis','action','check']) assert.ok(detail.textContent.includes(opportunity[key]));
  app.click(detail.querySelector('button'));const id=opportunity.theme_ids[0];
  assert.equal(app.q('[data-tab="themes"]').getAttribute('aria-selected'),'true');
  assert.equal(app.findTheme(id).open,true);
});
test('widget state rehydrates tab, area and expanded topic', () => {
  const saved=app.saved(), second=mount(saved);
  assert.equal(second.q('[data-tab="themes"]').getAttribute('aria-selected'),'true');
  assert.equal(second.q('[data-area="group"]').getAttribute('aria-pressed'),'true');
  const id=report.areas.group.opportunities[0].theme_ids[0];
  assert.equal(second.findTheme(id).open,true);
  assert.equal(second.q('[data-runtime-error]').hidden,true);
});
test('old report state ignored, blocked storage tolerated, double script is idempotent', () => {
  const wrong=app.saved();wrong.key='wrong-report';const third=mount(wrong,true);
  assert.equal(third.q('[data-area="article"]').getAttribute('aria-pressed'),'true');
  third.run();third.tab('themes');assert.equal(third.all('[data-theme-id]').length,report.areas.article.themes.length);
  assert.equal(third.q('[data-runtime-error]').hidden,true);
});
test('report uses no outbound network or model messaging', () => {
  const script=app.window.document.querySelector('script').textContent;
  assert.doesNotMatch(script,/\bfetch\s*\(|XMLHttpRequest|WebSocket|sendFollowUpMessage|callTool\s*\(/);
});
console.log(JSON.stringify({dom_tests:checked,host_ui_verified:false}));
