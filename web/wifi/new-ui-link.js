// Add the same Wi-Fi page to TeslaUSB's alternative Vue header.
// The release ships compiled assets, so this keeps the upstream bundle intact.
(() => {
  function addLink() {
    const header = document.querySelector('.header');
    if (!header) return;
    const target = header.querySelector('.extra') ||
      header.querySelector('.bar > div:last-child');
    if (!target) return;
    for (const [id, label, href] of [
      ['car-wifi-new-ui-link', 'Wi-Fi', '/wifi/'],
      ['car-moonlight-new-ui-link', 'Moonlight', '/moonlight/']
    ]) {
      if (document.getElementById(id)) continue;
      const link = document.createElement('a');
      link.id = id;
      link.href = href;
      link.textContent = label;
      link.style.cssText = 'display:inline-block;margin-right:14px;color:#c40000;font-weight:600;text-decoration:none;';
      target.prepend(link);
    }
  }
  const observer = new MutationObserver(addLink);
  observer.observe(document.documentElement, {childList:true, subtree:true});
  addLink();
})();
