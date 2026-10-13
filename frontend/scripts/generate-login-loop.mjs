// Original Learning Radar decorative cross-sections. No offline 3D tools.
import {mkdir,writeFile} from 'node:fs/promises';
import {loginRibPose} from '../src/login-loop-motion.ts';
const ribs=Array.from({length:88},(_,i)=>{
  return {index:i,...loginRibPose(i/88)};
}).sort((a,b)=>a.depth-b.depth);
const f=n=>n.toFixed(2),groups=`<g class="lr-loop-ribs">${ribs.map(r=>`<use data-rib="${r.index}" href="#lr-loop-rib" transform="matrix(${r.matrix.map(f).join(' ')})"/>`).join('')}</g>`;
const labels=[{x:38,y:65,text:'01 / DISCOVER',d:'M158 125l-34-48H38',px:158,py:125},{x:459,y:319,text:'02 / UNDERSTAND',d:'M431 237l48 62h81',px:431,py:237},{x:145,y:346,text:'03 / RECORD',d:'M303 194v133h-80',px:303,py:194}];
// Default clips are fully open, preserving readable no-enhancement SVG labels.
const clips=labels.map((l,i)=>`<clipPath id="lr-loop-label-${i}" clipPathUnits="userSpaceOnUse"><rect data-annotation-clip="${i}" x="${l.x-1}" y="${l.y-12}" width="145" height="16"/></clipPath>`).join('');
const annotations=`<g class="lr-loop-annotations" stroke="#151714" stroke-width=".8">${labels.map((l,i)=>`<path data-annotation-line="${i}" pathLength="1" d="${l.d}"/><circle data-annotation-point="${i}" cx="${l.px}" cy="${l.py}" r="2" fill="#151714"/>`).join('')}</g><g class="lr-loop-labels" fill="#151714" font-family="monospace" font-size="10" letter-spacing="1">${labels.map((l,i)=>`<text data-annotation-text="${i}" x="${l.x}" y="${l.y}" clip-path="url(#lr-loop-label-${i})">${l.text}</text>`).join('')}</g>`;
const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 390" fill="none" aria-hidden="true">
<defs><linearGradient id="lr-loop-face" x1="-.7" y1="-1" x2=".8" y2="1" gradientUnits="userSpaceOnUse"><stop stop-color="#faf7ee"/><stop offset=".48" stop-color="#e5ddce"/><stop offset="1" stop-color="#c7baa3"/></linearGradient><linearGradient id="lr-loop-edge"><stop stop-color="#b6a58a"/><stop offset="1" stop-color="#ebe4d7"/></linearGradient><g id="lr-loop-rib"><circle cx=".025" cy=".08" r="1" fill="url(#lr-loop-edge)"/><circle r="1" fill="url(#lr-loop-face)" stroke="#f4efe4" stroke-width=".025"/></g></defs>
<defs>${clips}</defs>${groups}${annotations}</svg>`;
await mkdir('public/artwork',{recursive:true});await writeFile('public/artwork/login-loop.svg',svg);
console.log('login loop: 88 cross-sections on the intro contour, '+Buffer.byteLength(svg)+' bytes');
