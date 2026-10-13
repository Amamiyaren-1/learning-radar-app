"""Original short glass/contact cues; no recordings or reference PV samples."""
from pathlib import Path
import math, struct, wave
ROOT=Path(__file__).resolve().parents[1]/'public'/'audio'
RATE=24000
for name,frequency,seconds in [('click',820,.055),('switch',1480,.085),('open',1120,.18),('back',980,.11)]:
    values=[]
    for index in range(round(RATE*seconds)):
        t=index/RATE
        attack=min(1,t/.002)
        tail=max(0,1-t/seconds)**3
        # Unequal glass modes and a short contact transient, all generated here.
        signal=.18*attack*tail*(math.sin(2*math.pi*frequency*t)+.24*math.sin(2*math.pi*frequency*1.47*t))
        if name=='open':signal+=.06*min(1,t/.012)*tail*math.sin(2*math.pi*560*t)
        values.append(struct.pack('<h',round(max(-1,min(1,signal))*32767)))
    with wave.open(str(ROOT/(name+'.wav')),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(RATE);out.writeframes(b''.join(values))
