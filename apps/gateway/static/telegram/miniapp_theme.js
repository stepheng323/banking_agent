(function () {
  let themeListenerInstalled = false;
  let mediaListenerInstalled = false;
  let lastOptions = {};

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function parseColor(input, fallback) {
    const value = (input || fallback || "").trim();
    if (!value) return [255, 255, 255];

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

    return parseColor(fallback || "#ffffff", "#ffffff");
  }

  function toHex(rgb) {
    return (
      "#" +
      rgb
        .map((n) => clamp(Math.round(n), 0, 255).toString(16).padStart(2, "0"))
        .join("")
    );
  }

  function rgba(rgb, alpha) {
    return `rgba(${clamp(Math.round(rgb[0]), 0, 255)},${clamp(Math.round(rgb[1]), 0, 255)},${clamp(
      Math.round(rgb[2]),
      0,
      255,
    )},${clamp(alpha, 0, 1)})`;
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

  function preferredScheme(tg) {
    const tgScheme = (tg && typeof tg.colorScheme === "string" ? tg.colorScheme : "").toLowerCase();
    if (tgScheme === "dark" || tgScheme === "light") return tgScheme;
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
    return "light";
  }

  function colorParam(params, key, fallback) {
    return parseColor(params && params[key], fallback);
  }

  function installThemeListeners(tg) {
    if (tg && typeof tg.onEvent === "function" && !themeListenerInstalled) {
      themeListenerInstalled = true;
      tg.onEvent("themeChanged", function () {
        applyTheme(lastOptions);
      });
    }

    if (window.matchMedia && !mediaListenerInstalled) {
      mediaListenerInstalled = true;
      const media = window.matchMedia("(prefers-color-scheme: dark)");
      const listener = function () {
        applyTheme(lastOptions);
      };
      if (typeof media.addEventListener === "function") {
        media.addEventListener("change", listener);
      } else if (typeof media.addListener === "function") {
        media.addListener(listener);
      }
    }
  }

  function applyTheme(options) {
    lastOptions = options || {};
    const opts = lastOptions;
    const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
    const params = (tg && tg.themeParams) || {};
    const scheme = preferredScheme(tg);
    const fallbackDark = scheme === "dark";
    const baseBg = fallbackDark ? "#17212b" : "#ffffff";
    const baseSecondary = fallbackDark ? "#0f1821" : "#f4f4f5";
    const baseSection = fallbackDark ? "#1f2c38" : "#ffffff";
    const baseText = fallbackDark ? "#f5f7fa" : "#111111";
    const baseHint = fallbackDark ? "#8f9aa6" : "#707579";
    const baseButton = opts.accentFallback || "#2ea6ff";
    const baseDanger = fallbackDark ? "#ff453a" : "#ff3b30";

    const bgRgb = colorParam(params, "bg_color", baseBg);
    const secondaryRgb = colorParam(params, "secondary_bg_color", baseSecondary);
    const sectionRgb = colorParam(params, "section_bg_color", toHex(mix(bgRgb, fallbackDark ? [255, 255, 255] : [0, 0, 0], fallbackDark ? 0.08 : 0.03)));
    const textRgb = colorParam(params, "text_color", baseText);
    const hintRgb = colorParam(params, "hint_color", baseHint);
    const buttonRgb = colorParam(params, "button_color", baseButton);
    const buttonTextRgb = colorParam(params, "button_text_color", "#ffffff");
    const linkRgb = colorParam(params, "link_color", toHex(buttonRgb));
    const dangerRgb = colorParam(params, "destructive_text_color", baseDanger);

    const darkTheme = isDark(bgRgb);
    const contrast = darkTheme ? [255, 255, 255] : [0, 0, 0];
    const border = rgba(contrast, darkTheme ? 0.1 : 0.08);
    const separator = rgba(contrast, darkTheme ? 0.08 : 0.07);
    const inputBg = toHex(sectionRgb);
    const disabled = mix(sectionRgb, hintRgb, darkTheme ? 0.28 : 0.18);
    const disabledText = rgba(hintRgb, 0.72);

    const root = document.documentElement.style;
    root.setProperty("color-scheme", darkTheme ? "dark" : "light");
    root.setProperty("--mini-bg", toHex(bgRgb));
    root.setProperty("--mini-secondary-bg", toHex(secondaryRgb));
    root.setProperty("--mini-section-bg", toHex(sectionRgb));
    root.setProperty("--mini-text", toHex(textRgb));
    root.setProperty("--mini-text-muted", toHex(hintRgb));
    root.setProperty("--mini-surface", toHex(sectionRgb));
    root.setProperty("--mini-surface-strong", toHex(secondaryRgb));
    root.setProperty("--mini-border", border);
    root.setProperty("--mini-separator", separator);
    root.setProperty("--mini-accent", toHex(buttonRgb));
    root.setProperty("--mini-link", toHex(linkRgb));
    root.setProperty("--mini-accent-soft", rgba(buttonRgb, darkTheme ? 0.18 : 0.12));
    root.setProperty("--mini-danger", toHex(dangerRgb));
    root.setProperty("--mini-success", "#34c759");
    root.setProperty("--mini-button-disabled", toHex(disabled));
    root.setProperty("--mini-button-disabled-text", disabledText);
    root.setProperty("--mini-input-border", border);
    root.setProperty("--mini-input-bg", inputBg);
    root.setProperty("--mini-choice-bg", toHex(sectionRgb));
    root.setProperty("--mini-progress-track", rgba(hintRgb, 0.24));
    root.setProperty("--mini-radio-border", rgba(hintRgb, 0.72));
    root.setProperty("--mini-btn-text", toHex(buttonTextRgb));

    document.documentElement.dataset.miniTheme = darkTheme ? "dark" : "light";

    if (tg) {
      try {
        tg.setBackgroundColor(toHex(bgRgb));
        tg.setHeaderColor(toHex(bgRgb));
      } catch (e) {}
    }

    installThemeListeners(tg);

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
