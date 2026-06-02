import { evaluate } from './src/connection.js';
const js = `(function(){
  var els=Array.from(document.querySelectorAll('[data-name="alert-item-description"]'));
  return JSON.stringify(els.map(function(e){return (e.textContent||'').trim();}));
})()`;
const r = await evaluate(js);
console.log(typeof r === 'string' ? r : JSON.stringify(r));
