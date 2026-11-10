(() => {
  function addLink() {
    const header=document.querySelector('.header');
    const target=header && (header.querySelector('.extra') || header.querySelector('.bar > div:last-child'));
    if(!target || document.getElementById('car-lte-new-ui-link')) return;
    const link=document.createElement('a');
    link.id='car-lte-new-ui-link'; link.href='/4g/'; link.textContent='4G';
    link.style.cssText='display:inline-block;margin-right:14px;color:#c40000;font-weight:600;text-decoration:none;';
    target.prepend(link);
  }
  new MutationObserver(addLink).observe(document.documentElement,{childList:true,subtree:true});
  addLink();
})();
