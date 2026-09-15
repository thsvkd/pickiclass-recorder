// Kollus 공식 제어 API만 사용한다. iframe 내부나 미디어 데이터에 접근하지 않는다.
(() => {
  if (location.origin !== 'https://pickiclass.com') return;
  if (window.__pickiclassPlayback) return;
  const state = window.__pickiclassPlayback = {status: 'connecting', position: 0};

  function playerFrame() {
    return document.querySelector('iframe#myClass');
  }

  function selectLesson(done) {
    const frame = playerFrame();
    if (!frame) {
      state.status = 'no_player';
      done();
      return;
    }
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      done();
    };
    frame.addEventListener('load', finish, {once: true});
    const index = window.__pickiclassLessonIndex;
    const rows = document.querySelectorAll('a.video_row');
    if (Number.isInteger(index) && index > 0 && rows[index]) {
      rows[index].click();
      setTimeout(finish, 2500);
      return;
    }
    const lessonId = window.__pickiclassLesson;
    if (lessonId && typeof video_url !== 'undefined') {
      const parts = String(lessonId).split(':');
      const url = video_url[parts[0]] && video_url[parts[0]][parts[1]] && video_url[parts[0]][parts[1]]['1'];
      if (typeof url === 'string' && url.indexOf('https://') === 0) {
        frame.src = url;
        setTimeout(finish, 2500);
        return;
      }
      state.status = 'lesson_not_found';
      finish();
      return;
    }
    finish();
  }

  function attachController() {
    const frame = playerFrame();
    if (!frame) {
      state.status = 'no_player';
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://file.kollus.com/wpcontroller/web-player-controller-client.3.0.7.min.js';
    script.integrity = 'sha384-s4QrCGcyFWEQmJsr0iK1A3HSjah8VE8Zq48k0jxHHUFOCsshRFRT1kNgj/xY/QPO';
    script.crossOrigin = 'anonymous';
    script.onerror = () => { state.status = 'controller_load_failed'; };
    script.onload = () => {
      try {
        const controller = new WebPlayerControllerClient({target_window: frame.contentWindow});
        const rememberError = () => {
          try {
            const detail = controller.get_error_detail();
            if (!detail || typeof detail !== 'object') return;
            if (detail.code != null) state.error_code = detail.code;
            const message = typeof detail.message === 'string' ? detail.message : '';
            if (message) state.error_message = message.slice(0, 200);
            if (/1002|-1002|캡처|캡쳐|원격|Remote|녹화/i.test(String(detail.code) + message)) {
              state.error_kind = 'capture_block';
            }
          } catch (_) {}
        };
        for (const event of ['loaded', 'ready', 'play', 'pause', 'done']) {
          controller.on(event, () => {
            state.status = event;
            if (event === 'pause' || event === 'done') rememberError();
          });
        }
        controller.on('error', () => {
          state.status = 'error';
          rememberError();
        });
        controller.on('progress', (percent, position, duration) => {
          if (Number.isFinite(position) && Number.isFinite(duration)) {
            state.position = position;
            state.duration = duration;
            if (state.status !== 'error') state.status = 'progress';
          }
        });
        const tryPlay = () => {
          try { controller.set_next_episode_auto(false); } catch (_) {}
          try { controller.set_volume(100); } catch (_) {}
          try { controller.play(); } catch (_) {}
        };
        controller.on('ready', tryPlay);
        state.status = 'controller_loaded';
        tryPlay();
        setInterval(() => {
          rememberError();
          try {
            const progress = controller.get_progress();
            const position = Array.isArray(progress) ? progress[1] : progress && progress.position;
            const duration = Array.isArray(progress) ? progress[2] : progress && progress.duration;
            if (Number.isFinite(position)) state.position = position;
            if (Number.isFinite(duration)) state.duration = duration;
            if (Number.isFinite(position) && position >= 1 && state.status !== 'error') state.status = 'progress';
          } catch (_) {}
          if (state.status === 'error' || state.status === 'done' || (state.position || 0) >= 1) return;
          tryPlay();
        }, 2000);
      } catch (_) { state.status = 'controller_failed'; }
    };
    document.head.appendChild(script);
  }

  selectLesson(attachController);
})();
