// Add the same Wi-Fi page to TeslaUSB's alternative Vue header.
// The release ships compiled assets, so this keeps the upstream bundle intact.
(() => {
  function addLink() {
    if (document.getElementById('car-wifi-new-ui-link')) return;
    const header = document.querySelector('.header');
    if (!header) return;
    const target = header.querySelector('.extra') ||
      header.querySelector('.bar > div:last-child');
    if (!target) return;
    const link = document.createElement('a');
    link.id = 'car-wifi-new-ui-link';
    link.href = '/wifi/';
    link.textContent = 'Wi-Fi';
    link.style.cssText = 'display:inline-block;margin-right:14px;color:#c40000;font-weight:600;text-decoration:none;';
    target.prepend(link);
  }
  const observer = new MutationObserver(addLink);
  observer.observe(document.documentElement, {childList:true, subtree:true});
  addLink();
})();
