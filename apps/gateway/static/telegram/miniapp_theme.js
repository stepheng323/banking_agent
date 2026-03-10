(function () {
  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function parseColor(input, fallback) {
    const value = (input || fallback || "").trim();
    if (!value) return [13, 17, 23];

    const hex = value.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (hex) {
      const raw = hex[1];
      if (raw.length === 3) {
        return raw.split("").map((ch) => parseInt(ch + ch, 16));
      }
      return [
        parseInt(raw.slice(0, 2), 16),
        parseInt(raw.slice(2, 4), 16),
        parseInt(raw.slice(4, 6), 16),
      ];
    }

    const rgb = value.match(/^rgba?\(([^)]+)\)$/i);
    if (rgb) {
      const parts = rgb[1]
        .split(",")
        .map((item) => parseFloat(item.trim()))
        .slice(0, 3)
        .map((item) => clamp(Number.isFinite(item) ? item : 0, 0, 255));
      if (parts.length === 3) return parts;
    }

    return parseColor(fallback || "#0d1117", "#0d1117");
  }

  function toHex(rgb) {
    return (
      "#" +
      rgb
        .map((n) => clamp(Math.round(n), 0, 255).toString(16).padStart(2, "0"))
        .join("")
    );
  }

  function mix(a, b, ratio) {
    const t = clamp(ratio, 0, 1);
    return [
      a[0] + (b[0] - a[0]) * t,
      a[1] + (b[1] - a[1]) * t,
      a[2] + (b[2] - a[2]) * t,
    ];
  }

  function luminance(rgb) {
    const srgb = rgb.map((n) => n / 255);
    const linear = srgb.map((c) =>
      c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4),
    );
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  }

  function isDark(rgb) {
    return luminance(rgb) < 0.35;
  }

  function applyTheme(options) {
    const opts = options || {};
    const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
    const params = (tg && tg.themeParams) || {};

    const bgRgb = parseColor(params.bg_color, "#0d1117");
    const textRgb = parseColor(params.text_color, isDark(bgRgb) ? "#f4f6f8" : "#161b22");
    const hintRgb = parseColor(params.hint_color, isDark(bgRgb) ? "#9ea8b3" : "#6b7380");
    const buttonRgb = parseColor(params.button_color, opts.accentFallback || "#21d07a");
    const buttonTextRgb = parseColor(params.button_text_color, "#ffffff");

    const darkTheme = isDark(bgRgb);
    const white = [255, 255, 255];
    const black = [7, 10, 13];

    const surface = darkTheme ? mix(bgRgb, white, 0.09) : mix(bgRgb, black, 0.05);
    const surfaceStrong = darkTheme ? mix(bgRgb, white, 0.04) : mix(bgRgb, black, 0.09);
    const border = darkTheme ? "rgba(255,255,255,0.08)" : "rgba(18,25,35,0.12)";
    const inputBorder = darkTheme ? "rgba(255,255,255,0.18)" : "rgba(20,28,38,0.22)";
    const inputBg = darkTheme ? "rgba(7,10,13,0.42)" : "rgba(255,255,255,0.82)";
    const choiceBg = darkTheme ? "rgba(255,255,255,0.03)" : "rgba(255,255,255,0.9)";
    const progressTrack = darkTheme ? "rgba(255,255,255,0.12)" : "rgba(18,25,35,0.14)";
    const radioBorder = darkTheme ? "rgba(186,196,206,0.72)" : "rgba(82,92,104,0.64)";
    const accentSoft = toHex(mix(buttonRgb, bgRgb, darkTheme ? 0.72 : 0.8));
    const disabled = darkTheme ? mix(bgRgb, white, 0.32) : mix(bgRgb, black, 0.2);
    const disabledText = darkTheme ? "rgba(231,237,243,0.62)" : "rgba(40,46,54,0.56)";

    const root = document.documentElement.style;
    root.setProperty("--mini-bg", toHex(bgRgb));
    root.setProperty("--mini-text", toHex(textRgb));
    root.setProperty("--mini-text-muted", toHex(hintRgb));
    root.setProperty("--mini-surface", toHex(surface));
    root.setProperty("--mini-surface-strong", toHex(surfaceStrong));
    root.setProperty("--mini-border", border);
    root.setProperty("--mini-accent", toHex(buttonRgb));
    root.setProperty("--mini-accent-soft", `${accentSoft}66`);
    root.setProperty("--mini-button-disabled", toHex(disabled));
    root.setProperty("--mini-button-disabled-text", disabledText);
    root.setProperty("--mini-input-border", inputBorder);
    root.setProperty("--mini-input-bg", inputBg);
    root.setProperty("--mini-choice-bg", choiceBg);
    root.setProperty("--mini-progress-track", progressTrack);
    root.setProperty("--mini-radio-border", radioBorder);
    root.setProperty("--mini-btn-text", toHex(buttonTextRgb));

    if (tg) {
      try {
        tg.setBackgroundColor(toHex(bgRgb));
        tg.setHeaderColor(toHex(bgRgb));
      } catch (e) {}
    }

    return {
      darkTheme: darkTheme,
      background: toHex(bgRgb),
      accent: toHex(buttonRgb),
    };
  }

  window.TelegramMiniAppTheme = {
    applyTheme: applyTheme,
  };
})();
