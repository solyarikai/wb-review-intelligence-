/* WB Review Intelligence renderer 1.0.0. No network, model calls or HTML from data. */
(() => {
  'use strict';
  const root = document.getElementById('__ROOT_ID__');
  if (!root || root.dataset.ready === 'true') return;
  const q = selector => root.querySelector(selector);
  const showError = () => { q('[data-runtime-error]').hidden = false; };
  try {
    const payload = __REPORT_PAYLOAD__;
    const meta = __REPORT_META__;
    function unpack(value) {
      if (typeof value === 'number' && value < 0) return payload.strings[-value - 1];
      if (Array.isArray(value)) return value.map(unpack);
      if (value && typeof value === 'object') {
        if (Object.hasOwn(value, '!')) return value['!'];
        return Object.fromEntries(Object.entries(value).map(([key, item]) =>
          [payload.strings[parseInt(key, 36)], unpack(item)]));
      }
      return value;
    }
    const report = unpack(payload.tree);
    const areaKeys = Object.keys(report.areas);
    const tabs = ['overview', 'themes', 'questions', 'opportunities'];
    const contexts = {positive:'Хвалят', negative:'Критикуют', neutral:'Нейтральный контекст'};
    const roles = {seller:'Ответ продавца', buyer:'Ответ покупателя', platform:'Ответ площадки', unknown:'Ответ · автор не установлен'};
    const make = (tag, text, cls) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (cls) node.className = cls;
      return node;
    };
    const safe = fn => event => { try { fn(event); } catch (_) { showError(); } };
    const action = (text, fn, cls = 'ri-action') => {
      const button = make('button', text, cls + ' cursor-interaction');
      button.type = 'button'; button.addEventListener('click', safe(fn)); return button;
    };
    const number = value => Number.isFinite(value) ? value.toLocaleString('ru-RU') : '—';
    const percent = (count, total) => total > 0 ?
      (100 * count / total).toLocaleString('ru-RU', {minimumFractionDigits:2, maximumFractionDigits:2}) + '%' : '—';
    const date = value => {
      if (!value || !/^\d{4}-\d{2}-\d{2}/.test(value)) return 'Дата неизвестна';
      const [year, month, day] = value.slice(0, 10).split('-'); return day + '.' + month + '.' + year;
    };
    const norm = value => String(value || '').toLocaleLowerCase('ru-RU').replace(/ё/g, 'е').trim();
    const size = value => Number.isSafeInteger(value) && value >= 5 ? Math.min(value, 1000000) : 5;
    const storageKey = 'wb-ri:' + meta.report_sha256 + ':' + meta.renderer_sha256;
    let touched = false;
    let area = areaKeys.includes('article') ? 'article' : areaKeys[0];
    let tab = 'overview';
    const state = Object.fromEntries(areaKeys.map(key => [key, {
      filter:'all', search:'', qaSearch:'', qaLimit:20, scopeOpen:false,
      themes:Object.create(null), fields:Object.create(null), questions:Object.create(null), opportunities:Object.create(null)
    }]));
    const indexes = Object.fromEntries(areaKeys.map(key => {
      const data = report.areas[key];
      const quotes = new Map(data.quotes.map(item => [item.id, item]));
      return [key, {quotes, themes:new Map(data.themes.map(theme => [theme.id, theme])),
        search:new Map(data.themes.map(theme => [theme.id, norm([theme.title,
          ...theme.quote_ids.flatMap(id => [quotes.get(id).text, quotes.get(id).full_field_text])].join('\n'))]))}];
    }));
    const data = () => report.areas[area];
    const current = () => state[area];
    function snapshot() { return {application:'wb-review-intelligence', key:storageKey, area, tab, areas:state}; }
    function restore(saved) {
      if (!saved || saved.application !== 'wb-review-intelligence' || saved.key !== storageKey) return false;
      if (areaKeys.includes(saved.area)) area = saved.area;
      if (tabs.includes(saved.tab)) tab = saved.tab;
      for (const key of areaKeys) {
        const previous = saved.areas && saved.areas[key];
        if (!previous || typeof previous !== 'object') continue;
        const next = state[key], d = report.areas[key];
        next.filter = ['all', 'positive', 'negative'].includes(previous.filter) ? previous.filter : 'all';
        next.search = typeof previous.search === 'string' ? previous.search.slice(0, 2000) : '';
        next.qaSearch = typeof previous.qaSearch === 'string' ? previous.qaSearch.slice(0, 2000) : '';
        next.qaLimit = Math.max(20, size(previous.qaLimit)); next.scopeOpen = previous.scopeOpen === true;
        for (const theme of d.themes) {
          const old = previous.themes && previous.themes[theme.id];
          if (old) next.themes[theme.id] = {open:old.open === true, limit:size(old.limit)};
        }
        for (const [field, records] of [['fields', d.quotes], ['questions', d.questions], ['opportunities', d.opportunities]]) {
          for (const item of records) if (previous[field] && previous[field][item.id] === true) next[field][item.id] = true;
        }
      }
      return true;
    }
    function persist() {
      touched = true;
      const saved = snapshot();
      try { window.sessionStorage.setItem(storageKey, JSON.stringify(saved)); } catch (_) { /* sandbox storage is optional */ }
      try {
        if (typeof window.openai?.setWidgetState === 'function') {
          const pending = window.openai.setWidgetState(saved);
          if (pending && typeof pending.catch === 'function') pending.catch(() => {});
        }
      } catch (_) { /* host state support is optional */ }
    }
    try { restore(JSON.parse(window.sessionStorage.getItem(storageKey))); } catch (_) {}
    try { restore(window.openai?.widgetState); } catch (_) {}
    function grouped(theme) {
      const groups = new Map();
      for (const id of theme.quote_ids) {
        const quote = indexes[area].quotes.get(id);
        if (!groups.has(quote.review_id)) groups.set(quote.review_id, []);
        groups.get(quote.review_id).push(quote);
      }
      const result = [...groups.values()], search = norm(current().search);
      if (search) result.sort((a, b) =>
        Number(b.some(x => norm(x.text + '\n' + x.full_field_text).includes(search))) -
        Number(a.some(x => norm(x.text + '\n' + x.full_field_text).includes(search))));
      return result;
    }
    function goTab(next) {
      if (!tabs.includes(next)) return;
      tab = next;
      root.querySelectorAll('[data-tab]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.tab === tab)));
      root.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== tab; });
      renderPanel(); persist();
    }
    function goTheme(id) {
      if (!indexes[area].themes.has(id)) return;
      current().filter = 'all'; current().search = '';
      current().themes[id] = {open:true, limit:5}; goTab('themes');
      const card = [...q('[data-themes]').children].find(item => item.dataset.themeId === id);
      if (card && typeof card.scrollIntoView === 'function') card.scrollIntoView({block:'nearest'});
    }
    function overview() {
      const d = data(), host = q('[data-top-themes]');
      q('[data-summary]').replaceChildren(make('p', d.summary || 'Выводы для этой области не сформированы.'));
      host.replaceChildren();
      for (const context of ['positive', 'negative']) {
        const box = make('div', undefined, 'ri-box'); box.dataset.context = context;
        box.append(make('h3', context === 'positive' ? 'За что выбирают' : 'Что настораживает', 'ri-' + context));
        const themes = [...d.themes].filter(item => item.context === context).sort((a,b) => b.count - a.count).slice(0,3);
        for (const theme of themes) {
          const row = make('div', undefined, 'ri-bar-row');
          row.append(action(theme.title, () => goTheme(theme.id), 'ri-text-action'), make('strong', percent(theme.count, d.metrics.text), 'ri-bar-value'));
          const track = make('div', undefined, 'ri-track'); track.setAttribute('aria-hidden', 'true');
          const bar = make('span'); bar.style.width = String(d.metrics.text > 0 ? Math.min(100, 100 * theme.count / d.metrics.text) : 0) + '%'; track.append(bar);
          row.append(track, make('span', number(theme.count) + '/' + number(d.metrics.text), 'ri-muted ri-small ri-bar-value')); box.append(row);
        }
        if (!themes.length) box.append(make('p', 'Тем этого контекста в разметке нет.', 'ri-muted'));
        host.append(box);
      }
      q('[data-all-themes]').textContent = 'Все темы и подтверждения · ' + number(d.themes.length) + ' →';
    }
    function themes() {
      const d = data(), view = current(), host = q('[data-themes]'), search = norm(view.search);
      q('[data-theme-search]').value = view.search;
      root.querySelectorAll('[data-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.filter === view.filter)));
      const items = [...d.themes].filter(theme => (view.filter === 'all' || theme.context === view.filter) &&
        (!search || indexes[area].search.get(theme.id).includes(search))).sort((a,b) => b.count - a.count);
      q('[data-theme-count]').textContent = number(items.length) + ' из ' + number(d.themes.length);
      host.replaceChildren();
      for (const theme of items) {
        const ownerArea = area, viewState = view.themes[theme.id] || (view.themes[theme.id] = {open:false, limit:5});
        const detail = make('details', undefined, 'ri-theme'); detail.dataset.themeId = theme.id;
        const summary = make('summary', undefined, 'cursor-interaction');
        const title = make('div', undefined, 'ri-theme-title');
        title.append(make('div', theme.title), make('span', contexts[theme.context], 'ri-small ri-' + (theme.context === 'neutral' ? 'muted' : theme.context)));
        const value = make('div', undefined, 'ri-theme-value');
        value.append(make('strong', number(theme.count) + '/' + number(d.metrics.text)), make('span', percent(theme.count, d.metrics.text), 'ri-muted'));
        summary.append(title, value); const body = make('div'); detail.append(summary, body);
        function fill() {
          body.replaceChildren(); const groups = grouped(theme), limit = Math.min(viewState.limit, groups.length);
          if (theme.evidence_status !== 'complete' || groups.length < theme.count) {
            body.append(make('p', 'Подтверждены ' + groups.length + ' из ' + theme.count + '. ' + (theme.evidence_reason || 'Трассировка неполная.'), 'ri-small'));
          }
          for (const quotes of groups.slice(0, limit)) {
            const item = make('div', undefined, 'ri-quote'); item.dataset.evidenceReview = quotes[0].review_id;
            for (const quote of quotes) {
              item.append(make('blockquote', quote.text));
              if (quote.full_field_text !== quote.text) {
                const full = make('details', undefined, 'ri-full');
                full.append(make('summary', 'Полный текст поля', 'cursor-interaction'), make('div', quote.full_field_text, 'ri-field'));
                full.open = view.fields[quote.id] === true;
                full.addEventListener('toggle', safe(() => { if (!full.isConnected || area !== ownerArea) return; view.fields[quote.id] = full.open; persist(); }));
                item.append(full);
              }
            }
            item.append(make('div', date(quotes[0].date) + ' · WB ' + quotes[0].product_id, 'ri-quote-meta')); body.append(item);
          }
          body.append(make('p', 'Показано ' + limit + ' из ' + groups.length + ' подтверждённых отзывов', 'ri-muted ri-small'));
          if (limit < groups.length) {
            const buttons = make('div', undefined, 'ri-actions');
            buttons.append(action('Показать ещё', () => { viewState.limit = limit + 5; fill(); persist(); }),
              action('Показать все', () => { viewState.limit = groups.length; fill(); persist(); })); body.append(buttons);
          }
        }
        detail.addEventListener('toggle', safe(() => {
          if (!detail.isConnected || area !== ownerArea) return;
          viewState.open = detail.open;
          if (detail.open) { if (!body.children.length) fill(); } else body.replaceChildren(); persist();
        }));
        host.append(detail); if (viewState.open) { detail.open = true; fill(); }
      }
      if (!items.length) host.append(make('p', d.themes.length ? 'Совпадений нет. Измени поиск или фильтр.' : 'Темы в этой области не выделены.', 'ri-muted'));
    }
    function questions() {
      const d = data(), view = current(), host = q('[data-questions]'), search = norm(view.qaSearch);
      q('[data-qa-search]').value = view.qaSearch; host.replaceChildren(); q('[data-qa-more]').replaceChildren();
      const items = d.questions.filter(item => !search || norm([item.question, ...(item.answers || []).map(answer => answer.text)].join('\n')).includes(search));
      const limit = Math.min(items.length, view.qaLimit);
      q('[data-qa-count]').textContent = 'Показано ' + limit + ' из ' + items.length;
      for (const item of items.slice(0, limit)) {
        const ownerArea = area, detail = make('details', undefined, 'ri-box ri-question'); detail.dataset.questionId = item.id;
        detail.append(make('summary', item.question, 'cursor-interaction'));
        const fill = () => {
          if (detail.children.length > 1) return;
          detail.append(make('p', date(item.asked_at) + (item.product_id ? ' · WB ' + item.product_id : ''), 'ri-muted ri-small'));
          for (const answer of item.answers || []) detail.append(make('p', (roles[answer.role] || roles.unknown) + (answer.date ? ' · ' + date(answer.date) : ''), 'ri-muted ri-small'), make('blockquote', answer.text));
          if (!(item.answers || []).length) detail.append(make('p', 'В сохранённых данных ответа нет.', 'ri-muted'));
        };
        detail.addEventListener('toggle', safe(() => { if (!detail.isConnected || area !== ownerArea) return; view.questions[item.id] = detail.open; if (detail.open) fill(); persist(); }));
        host.append(detail); if (view.questions[item.id]) { detail.open = true; fill(); }
      }
      if (limit < items.length) q('[data-qa-more]').append(action('Ещё 20 вопросов', () => { view.qaLimit += 20; questions(); persist(); }), action('Показать все', () => { view.qaLimit = items.length; questions(); persist(); }));
      if (!items.length) host.append(make('p', d.questions.length ? 'Совпадений нет.' : 'Вопросы и ответы в сохранённой выгрузке отсутствуют. Это не подтверждает отсутствие вопросов у товара.', 'ri-muted'));
    }
    function opportunities() {
      const view = current(), host = q('[data-opportunities]'); host.replaceChildren();
      for (const item of data().opportunities) {
        const ownerArea = area, detail = make('details', undefined, 'ri-box ri-opportunity'); detail.dataset.opportunityId = item.id;
        detail.append(make('summary', item.title, 'cursor-interaction'));
        const fill = () => {
          if (detail.children.length > 1) return;
          for (const [key, label] of [['observation', 'Наблюдение'], ['hypothesis', 'Гипотеза'], ['action', 'Действие'], ['check', 'Как проверить']]) {
            const paragraph = make('p'); paragraph.append(make('strong', label + '. '), document.createTextNode(item[key] || 'Не сформировано в анализе.')); detail.append(paragraph);
          }
          const links = make('div', undefined, 'ri-actions');
          for (const id of item.theme_ids) { const theme = indexes[area].themes.get(id); if (theme) links.append(action(theme.title + ' →', () => goTheme(id))); }
          detail.append(links);
        };
        detail.addEventListener('toggle', safe(() => { if (!detail.isConnected || area !== ownerArea) return; view.opportunities[item.id] = detail.open; if (detail.open) fill(); persist(); }));
        host.append(detail); if (view.opportunities[item.id]) { detail.open = true; fill(); }
      }
      if (!data().opportunities.length) host.append(make('p', 'Возможности по сохранённому анализу не сформированы.', 'ri-muted'));
    }
    function renderPanel() {
      if (tab === 'overview') overview(); if (tab === 'themes') themes();
      if (tab === 'questions') questions(); if (tab === 'opportunities') opportunities();
    }
    function changeArea() {
      const d = data(), product = d.product || report.product || {}, scope = d.scope || {}, dates = d.dates || {};
      root.querySelectorAll('[data-area]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.area === area)));
      q('[data-product-label]').textContent = (area === 'article' ? 'АРТИКУЛ ' + (product.id || report.product?.id || 'не указан') : 'ГРУППА' + (scope.variant_count ? ' · ' + number(scope.variant_count) + ' вариантов' : '')) + (product.brand ? ' · ' + product.brand : '');
      q('[data-name]').textContent = product.name || (area === 'article' ? 'Исходный артикул' : 'Группа товаров');
      q('[data-analyzed]').textContent = number(d.metrics.analyzed); q('[data-returned]').textContent = number(d.metrics.returned);
      q('[data-empty]').textContent = number(d.metrics.empty) + ' без текста';
      q('[data-period]').textContent = date(dates.start) + ' — ' + date(dates.end) + ' · Тем: ' + number(d.themes.length);
      const coverage = scope.coverage || {}, ready = report.readiness || {};
      const source = ready.source_complete === true ? 'Охват источника подтверждён' : 'Полнота источника не подтверждена';
      q('[data-scope]').replaceChildren(make('p', source), make('p', coverage.basis || 'Основание охвата не указано.'), make('p', scope.description || ''),
        make('p', 'Обработано ' + number(d.metrics.analyzed) + '/' + number(d.metrics.text) + ' текстовых отзывов. Доказательства проверены. Проверка интерфейса в ChatGPT: ' + (ready.ui_status === 'PASS' ? 'выполнена для указанной версии.' : 'не подтверждена для этой версии.'), 'ri-small'));
      const scopeDetail = q('[data-scope-details]'); scopeDetail.open = current().scopeOpen;
      q('[data-footer]').textContent = 'Review Intelligence · Wildberries · Снимок ' + date(dates.generated_at) + ' · ' + source;
      q('[data-themes]').replaceChildren(); q('[data-questions]').replaceChildren(); q('[data-opportunities]').replaceChildren();
      root.querySelectorAll('[data-tab]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.tab === tab)));
      root.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== tab; });
      renderPanel();
    }
    root.querySelectorAll('[data-area]').forEach(button => button.addEventListener('click', safe(() => { area = button.dataset.area; changeArea(); persist(); })));
    root.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', safe(() => goTab(button.dataset.tab))));
    root.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', safe(() => { current().filter = button.dataset.filter; themes(); persist(); })));
    q('[data-all-themes]').addEventListener('click', safe(() => goTab('themes')));
    q('[data-theme-search]').addEventListener('input', safe(event => { current().search = event.target.value; themes(); persist(); }));
    q('[data-qa-search]').addEventListener('input', safe(event => { current().qaSearch = event.target.value; current().qaLimit = 20; questions(); persist(); }));
    q('[data-scope-details]').addEventListener('toggle', safe(() => { current().scopeOpen = q('[data-scope-details]').open; persist(); }));
    window.addEventListener('openai:set_globals', safe(() => { if (!touched && restore(window.openai?.widgetState)) changeArea(); }));
    changeArea(); root.querySelectorAll('button:disabled').forEach(button => { button.disabled = false; });
    q('[data-startup]').hidden = true; root.dataset.ready = 'true';
  } catch (_) { showError(); }
})();
