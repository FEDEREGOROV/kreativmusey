(function () {
  if (window.CreativeMuseumChatWidget) return;
  window.CreativeMuseumChatWidget = true;

  const script = document.currentScript;
  const widgetOrigin = script?.src
    ? new URL(script.src, window.location.href).origin
    : 'https://pflstat.ru';

  const style = document.createElement('style');
  style.textContent = `
    .cm-widget,
    .cm-widget * {
      box-sizing: border-box;
    }

    .cm-widget {
      position: fixed;
      right: 24px;
      bottom: 24px;
      z-index: 2147483000;
      font-family: Arial, Helvetica, sans-serif;
    }

    .cm-widget__launcher {
      display: grid;
      place-items: center;
      width: 72px;
      height: 72px;
      padding: 6px;
      border: 1px solid rgba(54, 97, 39, 0.18);
      border-radius: 50%;
      background: #ffffff;
      box-shadow: 0 12px 34px rgba(35, 70, 33, 0.24);
      cursor: pointer;
      transition: transform 160ms ease, box-shadow 160ms ease;
      -webkit-tap-highlight-color: transparent;
    }

    .cm-widget__launcher:hover {
      transform: translateY(-2px) scale(1.03);
      box-shadow: 0 16px 40px rgba(35, 70, 33, 0.3);
    }

    .cm-widget__launcher:focus-visible,
    .cm-widget__close:focus-visible {
      outline: 3px solid rgba(96, 171, 49, 0.45);
      outline-offset: 3px;
    }

    .cm-widget__launcher-image {
      display: block;
      width: 58px;
      height: 58px;
      object-fit: contain;
    }

    .cm-widget__panel {
      position: absolute;
      right: 0;
      bottom: 88px;
      display: flex;
      flex-direction: column;
      width: min(420px, calc(100vw - 32px));
      height: min(680px, calc(100dvh - 128px));
      min-height: 440px;
      overflow: hidden;
      border: 1px solid #dce4d8;
      border-radius: 24px;
      background: #ffffff;
      box-shadow: 0 24px 70px rgba(21, 45, 24, 0.24);
      opacity: 0;
      pointer-events: none;
      transform: translateY(14px) scale(0.97);
      transform-origin: right bottom;
      transition: opacity 180ms ease, transform 180ms ease;
    }

    .cm-widget--open .cm-widget__panel {
      opacity: 1;
      pointer-events: auto;
      transform: translateY(0) scale(1);
    }

    .cm-widget__header {
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 64px;
      padding: 10px 12px 10px 16px;
      border-bottom: 1px solid #e5eae2;
      background: #ffffff;
    }

    .cm-widget__header-image {
      width: 42px;
      height: 42px;
      object-fit: contain;
    }

    .cm-widget__header-copy {
      min-width: 0;
      flex: 1;
    }

    .cm-widget__title {
      margin: 0;
      color: #101411;
      font-size: 16px;
      font-weight: 700;
      line-height: 1.2;
    }

    .cm-widget__status {
      margin: 3px 0 0;
      color: #4f7d45;
      font-size: 12px;
      line-height: 1.2;
    }

    .cm-widget__close {
      display: grid;
      place-items: center;
      width: 40px;
      height: 40px;
      padding: 0;
      border: 0;
      border-radius: 50%;
      color: #313832;
      background: #f2f5f0;
      font: 300 28px/1 Arial, Helvetica, sans-serif;
      cursor: pointer;
    }

    .cm-widget__frame {
      display: block;
      width: 100%;
      min-height: 0;
      flex: 1;
      border: 0;
      background: #ffffff;
    }

    @media (max-width: 640px) {
      .cm-widget {
        right: 14px;
        bottom: 14px;
      }

      .cm-widget__launcher {
        width: 64px;
        height: 64px;
      }

      .cm-widget__launcher-image {
        width: 52px;
        height: 52px;
      }

      .cm-widget__panel {
        position: fixed;
        right: 10px;
        bottom: 88px;
        left: 10px;
        width: auto;
        height: calc(100dvh - 108px);
        min-height: 420px;
        border-radius: 20px;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      .cm-widget__launcher,
      .cm-widget__panel {
        transition: none;
      }
    }
  `;
  document.head.appendChild(style);

  const root = document.createElement('div');
  root.className = 'cm-widget';
  root.innerHTML = `
    <section class="cm-widget__panel" aria-label="Чат помощника конкурса" aria-hidden="true">
      <header class="cm-widget__header">
        <img class="cm-widget__header-image" src="${widgetOrigin}/mascot/mascot-favicon-v2.png" alt="" />
        <div class="cm-widget__header-copy">
          <p class="cm-widget__title">Помощник конкурса</p>
          <p class="cm-widget__status">Готов ответить на вопросы</p>
        </div>
        <button class="cm-widget__close" type="button" aria-label="Закрыть чат">×</button>
      </header>
      <iframe
        class="cm-widget__frame"
        src="${widgetOrigin}/embed"
        title="Помощник конкурса «Креативный музей»"
        loading="lazy"
        allow="clipboard-write"
      ></iframe>
    </section>
    <button class="cm-widget__launcher" type="button" aria-label="Открыть чат" aria-expanded="false">
      <img class="cm-widget__launcher-image" src="${widgetOrigin}/mascot/mascot-favicon-v2.png" alt="" />
    </button>
  `;
  document.body.appendChild(root);

  const launcher = root.querySelector('.cm-widget__launcher');
  const closeButton = root.querySelector('.cm-widget__close');
  const panel = root.querySelector('.cm-widget__panel');

  function setOpen(open) {
    root.classList.toggle('cm-widget--open', open);
    launcher.setAttribute('aria-expanded', String(open));
    launcher.setAttribute('aria-label', open ? 'Закрыть чат' : 'Открыть чат');
    panel.setAttribute('aria-hidden', String(!open));
    if (open) closeButton.focus({ preventScroll: true });
  }

  launcher.addEventListener('click', () => {
    setOpen(!root.classList.contains('cm-widget--open'));
  });
  closeButton.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') setOpen(false);
  });
})();
