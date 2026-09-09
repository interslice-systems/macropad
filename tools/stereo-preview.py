#!/usr/bin/env python3
"""Build the standalone OLED preview from firmware font/geometry constants."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'shared'))
import km_stereo as stereo

DATA = json.dumps({'font': stereo.FONT, 'face': sorted(stereo.backdrop()),
                   'pageMs': stereo.PAGE_MS})
HTML = '''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Operator · Stereo display</title>
<style>
:root{color-scheme:dark;font-family:ui-monospace,monospace;background:#181a1b;color:#e5e6e2}
*{box-sizing:border-box}body{max-width:900px;margin:0 auto;padding:40px 24px}
header{border-bottom:1px solid #535754;padding-bottom:20px;margin-bottom:32px}
h1{font-size:26px;letter-spacing:.15em;margin:0 0 10px}p{line-height:1.7;color:#b6bcb8;max-width:75ch}
.receiver{border:1px solid #666d68;border-radius:12px;padding:24px;background:linear-gradient(#353a37,#292d2b);box-shadow:0 16px 36px #0006}
.engraved{display:flex;justify-content:space-between;gap:12px;font-size:12px;letter-spacing:.14em;margin-bottom:20px;color:#c6cbc6}
.glass{background:#000;padding:24px 0;border:2px solid #111;border-radius:5px;display:flex;justify-content:center;overflow:hidden}
canvas{image-rendering:pixelated;display:block;width:100%;max-width:640px;aspect-ratio:2;background:#000}
.controls{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:26px 0 14px}
label{display:block;font-size:13px;color:#c9ceca}input,select,button{font:inherit;color:#fff;background:#111513;border:1px solid #69736b;border-radius:4px;padding:10px}
input,select{width:100%;margin-top:8px}button{cursor:pointer;margin:0 8px 8px 0}button:hover{background:#38443b}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #fff;outline-offset:3px}
.native{margin-top:24px;display:flex;gap:24px;align-items:center}.native canvas{width:128px;min-width:128px;height:64px}.native p{font-size:12px}
footer{margin-top:28px;font-size:12px;color:#b6bcb8;line-height:1.7}
@media(max-width:540px){body{padding:24px 14px}.receiver{padding:12px}.controls{grid-template-columns:1fr}.engraved{font-size:10px}.glass{padding:16px 0}}
</style>
<header><h1>OPERATOR</h1><div>Shipboard stereo / OLED study 01</div></header>
<p>A little hi-fi faceplate for the desk. Two lines of now-playing text, sixteen segmented bands, falling peak caps, and a frequency scale. Every lit square below is one OLED pixel.</p>
<div class="receiver"><div class="engraved"><span>OPERATOR / SPECTRUM ANALYZER</span><span>50 Hz — 16 kHz</span></div>
<div class="glass"><canvas id="oled" width="128" height="64" role="img" aria-label="Animated preview of the Operator OLED spectrum and song readout"></canvas></div>
<div class="native"><canvas id="native" width="128" height="64" aria-label="128 by 64 pixel preview"></canvas><p>128 × 64 · monochrome<br>Unscaled pixels at left; physical size depends on your monitor.</p></div></div>
<div class="controls"><label>Track title<input id="title" maxlength="96" value="Summer lofi radio music to put you in a better mood"></label><label>Artist<input id="artist" maxlength="96" value="Lofi Girl"></label></div>
<button id="lofi">Lofi radio</button><button id="jazz">Jazz record</button><button id="fallback">No metadata</button><button id="pause" aria-pressed="false">Freeze animation</button>
<p id="readout" aria-live="polite"></p>
<footer>The display uses the firmware's exact 5×7 glyphs and bezel geometry. Audio here is simulated; it does not access your microphone or desktop. Long lines hold each page for three seconds. On the pad, music drives the bars; silence returns to rain, and bells and workspace alerts take priority. The white OLED has no amber or green lighting.</footer>
<script>
const data=__DATA__;
const canvas=document.getElementById('oled'),ctx=canvas.getContext('2d');
const native=document.getElementById('native').getContext('2d');
const title=document.getElementById('title'),artist=document.getElementById('artist');
let epoch=performance.now(),paused=false,frozen=0,last=0,peaks=Array(16).fill(0),tops=Array(16).fill(0),raised=Array(16).fill(0),lastText='';
function pages(s){s=s.normalize('NFKD').replace(/[^ -~]/g,'').toUpperCase().trim();let out=[];while(s.length>20){let cut=s.lastIndexOf(' ',20);if(cut<=0)cut=20;out.push(s.slice(0,cut));s=s.slice(cut).trimStart()}out.push(s);return out}
function text(s,x,y){for(let i=0;i<s.length;i++){const rows=data.font[s[i]]||data.font['?'];rows.forEach((mask,r)=>{for(let c=0;c<5;c++)if(mask&(1<<(4-c)))ctx.fillRect(x+i*6+c,y+r,1,1)})}}
function draw(now){if(now-last<50){requestAnimationFrame(draw);return}last=now;const t=paused?frozen:now-epoch;ctx.fillStyle='#000';ctx.fillRect(0,0,128,64);ctx.fillStyle='#fff';data.face.forEach(([x,y])=>ctx.fillRect(x,y,1,1));
 const a=pages(title.value||'System audio'),b=pages(artist.value||'Operator'),page=Math.floor(t/data.pageMs);
 const lines=[a[page%a.length],b[page%b.length]];text(lines[0],8,0);text(lines[1],8,9);
 if(lines.join(' / ')!==lastText){lastText=lines.join(' / ');document.getElementById('readout').textContent=lastText}
 for(let i=0;i<16;i++){const v=Math.max(0,Math.min(16,Math.round(6+4*Math.sin(t/730+i*.35)+3*Math.sin(t/190+i*.89)+(15-i)*.14)));let peak=Math.max(0,tops[i]-Math.floor(Math.max(0,t-raised[i]-1000)/80));if(v&&v>=peak){tops[i]=peak=v;raised[i]=t}peaks[i]=peak;const bar=Math.ceil(v/2),cap=Math.ceil(peak/2);for(let row=0;row<8;row++){const y=22+row*4,x=9+i*7;if(8-row<=bar)ctx.fillRect(x,y+2,5,2);if(8-row===cap)ctx.fillRect(x,y,5,1)}}
 native.drawImage(canvas,0,0);requestAnimationFrame(draw)}
function reset(){epoch=performance.now();frozen=0;tops.fill(0);raised.fill(0)}
title.addEventListener('input',reset);artist.addEventListener('input',reset);
function preset(t,a){title.value=t;artist.value=a;reset()}
document.getElementById('lofi').onclick=()=>preset('Summer lofi radio music to put you in a better mood','Lofi Girl');
document.getElementById('jazz').onclick=()=>preset('So What','Miles Davis');
document.getElementById('fallback').onclick=()=>preset('','');
document.getElementById('pause').onclick=function(){if(!paused){frozen=performance.now()-epoch;paused=true}else{epoch=performance.now()-frozen;paused=false}this.textContent=paused?'Resume animation':'Freeze animation';this.setAttribute('aria-pressed',String(paused))};
if(matchMedia('(prefers-reduced-motion: reduce)').matches){paused=true;document.getElementById('pause').textContent='Resume animation';document.getElementById('pause').setAttribute('aria-pressed','true')}
requestAnimationFrame(draw);
</script></html>
'''

target = ROOT / 'docs/stereo-preview.html'
target.write_text(HTML.replace('__DATA__', DATA))
print(target)
