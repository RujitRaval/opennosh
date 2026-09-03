(() => {
  "use strict";

  if (window.parent === window || !document.referrer) return;

  let parentOrigin;
  try {
    const referrer = new URL(document.referrer);
    if (referrer.protocol !== "https:" && referrer.protocol !== "http:") return;
    parentOrigin = referrer.origin;
  } catch {
    return;
  }

  const publishHeight = () => {
    const measured = Math.ceil(document.documentElement.getBoundingClientRect().height);
    const height = Math.max(160, Math.min(1200, measured));
    window.parent.postMessage(
      { schema_version: "1.0", type: "opennosh.embed.resize", height },
      parentOrigin,
    );
  };

  publishHeight();
  if ("ResizeObserver" in window) new ResizeObserver(publishHeight).observe(document.documentElement);
})();
