// Preview bridge injected into every HTML preview iframe (HtmlPreview.vue).
// The iframe runs sandboxed without allow-same-origin (opaque origin), so the
// host cannot touch its DOM directly. This script, inlined into the srcdoc,
// reports the content height, executes PNG captures on request, and applies
// theme variables / feedback CSS when the comment layer is activated.
// Companion of feedbackBridgeContent.ts (the comment engine itself).
export const PREVIEW_CORE_JS = `(function(){
if(window.__pebblePreviewBridge)return;window.__pebblePreviewBridge=true;
function post(type,data){try{window.parent.postMessage(Object.assign({type:type},data||{}),'*')}catch(e){}}
function ensureStyles(){try{if(document.getElementById('__pebble_preview_style'))return;var s=document.createElement('style');s.id='__pebble_preview_style';s.textContent='html, body { overflow: clip !important; height: auto !important; } * { max-height: none !important; } body { min-width: 800px !important; }';(document.head||document.documentElement).appendChild(s)}catch(e){}}
function reportHeight(){try{var h=Math.max(document.documentElement?document.documentElement.scrollHeight:0,document.body?document.body.scrollHeight:0);post('pebble-preview-height',{height:h})}catch(e){}}
function boot(){ensureStyles();reportHeight();if(window.ResizeObserver){try{new ResizeObserver(function(){reportHeight()}).observe(document.documentElement)}catch(e){}}setInterval(reportHeight,1000);window.addEventListener('load',reportHeight);setTimeout(reportHeight,100);setTimeout(reportHeight,400)}
if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',boot)}else{boot()}
function doCapture(id){try{var el=document.querySelector('.poster')||document.body;var lib=window.htmlToImage;if(!lib||!lib.toPng){post('pebble-preview-capture-result',{id:id,ok:false,error:'capture library unavailable'});return}lib.toPng(el,{pixelRatio:2,backgroundColor:'#000'}).then(function(url){post('pebble-preview-capture-result',{id:id,ok:true,dataUrl:url})}).catch(function(err){post('pebble-preview-capture-result',{id:id,ok:false,error:String(err)})})}catch(e){post('pebble-preview-capture-result',{id:id,ok:false,error:String(e)})}}
window.addEventListener('message',function(e){if(e.source!==window.parent)return;if(!e.data||typeof e.data!=='object')return;var d=e.data;if(d.type==='pebble-preview-capture'){doCapture(d.id)}else if(d.type==='pebble-feedback-activate'){try{var vars=d.vars||{};var css=':root{';for(var k in vars){if(Object.prototype.hasOwnProperty.call(vars,k)){css+=k+':'+vars[k]+';'}}css+='}';var v=document.getElementById('__pebble_fb_vars');if(!v){v=document.createElement('style');v.id='__pebble_fb_vars';(document.head||document.documentElement).appendChild(v)}v.textContent=css;if(!document.getElementById('__pebble_fb_css')&&window.__PEBBLE_FEEDBACK_CSS){var c=document.createElement('style');c.id='__pebble_fb_css';c.textContent=window.__PEBBLE_FEEDBACK_CSS;(document.head||document.documentElement).appendChild(c)}}catch(err){}}});
post('pebble-preview-ready');
})()`
