"""Render three real source excerpts; no speech recognition or generated footage.
Uses the inspected, MIT-licensed ClipKaro facecam geometry/filter functions.
"""
from pathlib import Path
import ast
import json
import math
import os
import re
import struct
import subprocess
import urllib.request
import wave
import zipfile


def run(args, timeout=360):
    p = subprocess.run([str(x) for x in args], capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-7000:])
    return p.stdout


def probe(path):
    return json.loads(run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',path]))


def timecode(t):
    t = float(t)
    return f'{int(t)//3600:02}:{int(t)//60%60:02}:{t%60:06.3f}'


def load_clipkaro():
    root = Path(os.environ.get('CLIPKARO_ROOT','ClipKaro'))
    tree = ast.parse((root/'clipper/facecam.py').read_text())
    env = {'W':1080,'H':1920}
    funcs = []
    for node in tree.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            try:
                env[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError,TypeError):
                pass
        elif isinstance(node,ast.FunctionDef) and node.name in {'clamp','geometry','filter_chain'}:
            funcs.append(node)
    if len(funcs)!=3:
        raise RuntimeError('Expected ClipKaro facecam functions were not found.')
    exec(compile(ast.Module(body=funcs,type_ignores=[]),'ClipKaro/clipper/facecam.py','exec'),env)
    return root,env


def hook_ass(path, text):
    parts=str(text).replace('{','').replace('}','').replace('\\','').splitlines()
    styled=parts[0]
    if len(parts)>1:
        styled += r'\N{\c&H0000FFFF&}'+' '.join(parts[1:])
    header='''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Hook,Montserrat ExtraBold,74,&H00FFFFFF,&H0000FFFF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,5,2,8,80,160,140,1
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
'''
    path.write_text(header+'Dialogue: 0,0:00:00.00,0:00:03.50,Hook,,0,0,0,,{\\an8\\pos(510,140)\\fad(80,120)}'+styled+'\n',encoding='utf-8')


def pop(path):
    sr=48000
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr)
        samples=[]
        for i in range(int(.13*sr)):
            t=i/sr
            value=.035*math.sin(2*math.pi*(520*t-1350*t*t))*math.exp(-32*t)
            samples.append(struct.pack('<h',int(value*32767)))
        w.writeframes(b''.join(samples))


def main():
    work=Path('render_work');work.mkdir(exist_ok=True)
    export=Path('exports');export.mkdir(exist_ok=True)
    with urllib.request.urlopen(os.environ['BUNDLE_URL'],timeout=90) as response,open(work/'sources.zip','wb') as out:
        total=0
        while True:
            chunk=response.read(1024*1024)
            if not chunk:break
            total+=len(chunk)
            if total>180*1024**2:raise RuntimeError('Source bundle exceeds safety cap.')
            out.write(chunk)
    with zipfile.ZipFile(work/'sources.zip') as archive:
        manifest=json.loads(archive.read('manifest.json'))
        clips=manifest.get('clips',[])
        if len(clips)!=3:raise RuntimeError('Exactly three clips are required.')
        for row in clips:
            name=f"source_{int(row['id'])}.mp4"
            if row['file']!=name:raise RuntimeError('Unexpected source filename.')
            entry=archive.getinfo(name)
            if entry.file_size>80*1024**2:raise RuntimeError('Source excerpt exceeds safety cap.')
            with archive.open(entry) as src,open(work/name,'wb') as dst:
                while True:
                    chunk=src.read(1024*1024)
                    if not chunk:break
                    dst.write(chunk)
    root,fc=load_clipkaro()
    pop_file=work/'soft_hook_pop.wav';pop(pop_file)
    results=[]
    ranges=[]
    for row in clips:
        cid=int(row['id']);duration=float(row['duration']);trim=float(row['trim_start'])
        if not 20<=duration<=30:raise RuntimeError('Clip duration outside 20-30 seconds.')
        a,b=float(row['source_start']),float(row['source_end'])
        if any(a<y and b>x for x,y in ranges):raise RuntimeError('Source moments overlap.')
        ranges.append((a,b))
        source=work/row['file'];info=probe(source)
        video=next(s for s in info['streams'] if s['codec_type']=='video')
        sw,sh=int(video['width']),int(video['height'])
        c=row['facecam']
        cam=fc['clamp']({'x':float(c['x'])*sw,'y':float(c['y'])*sh,'w':float(c['w'])*sw,'h':float(c['h'])*sh,'shape':'rect'},sw,sh)
        if cam['w']<50 or cam['h']<50:raise RuntimeError('Invalid original facecam crop.')
        g=fc['geometry'](cam,sw,sh)
        chain=fc['filter_chain']('base','stack',cam,g,sw,sh,30,None)
        top_h=fc['TOP_H']
        original=f"[stack_g]crop={g['gw']}:{sh}:{g['gx']}:0,scale=1080:{top_h}:flags=lanczos,setsar=1[stack_top]"
        if original not in chain:raise RuntimeError('ClipKaro layout changed; refusing an unverified replacement.')
        # Keep the entire gameplay image instead of losing side actions in a narrow crop.
        replacement=(f'[stack_g]split=2[gamefit][gameblur];'
            f'[gameblur]scale=1080:{top_h}:force_original_aspect_ratio=increase,crop=1080:{top_h},boxblur=24:2[gamebg];'
            f'[gamefit]scale=1080:{top_h}:force_original_aspect_ratio=decrease[gamefg];'
            '[gamebg][gamefg]overlay=(W-w)/2:(H-h)/2,setsar=1[stack_top]')
        chain=chain.replace(original,replacement)
        # A gentle punch-in of the real facecam, not a fabricated reaction or replay.
        cam_step=(f"[stack_c]crop={cam['w']}:{cam['h']}:{cam['x']}:{cam['y']},"
                  f"scale={g['cam_w']}:{g['cam_h']}:flags=lanczos,setsar=1[stack_cam]")
        if cam_step in chain:
            peak=max(0,min(duration,float(row.get('punch_at',10))))
            z=f"1+0.035*exp(-pow((on/30-{peak:.3f})/0.48,2))"
            new_cam=(f"[stack_c]crop={cam['w']}:{cam['h']}:{cam['x']}:{cam['y']},"
                f"scale={g['cam_w']}:{g['cam_h']}:flags=lanczos,"
                f"zoompan=z='{z}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={g['cam_w']}x{g['cam_h']}:fps=30,"
                'setsar=1[stack_cam]')
            chain=chain.replace(cam_step,new_cam)
        ass=work/f'hook_{cid}.ass';hook_ass(ass,row['hook'])
        fonts=(root/'assets/fonts').resolve().as_posix()
        graph=('[0:v]fps=30,setpts=PTS-STARTPTS[base];'+chain+
            f";[stack]subtitles=filename='{ass.resolve().as_posix()}':fontsdir='{fonts}',setsar=1,format=yuv420p[v];"
            '[0:a]asetpts=PTS-STARTPTS,highpass=f=65,loudnorm=I=-14:TP=-1.5:LRA=11[voice];'
            '[1:a]adelay=100|100[pop];[voice][pop]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]')
        name=f'Clip_{cid}_Best_Moment_{cid}.mp4';destination=export/name
        run(['ffmpeg','-hide_banner','-loglevel','warning','-nostdin','-y','-ss',trim,'-i',source,'-i',pop_file,
             '-t',duration,'-filter_complex_threads','2','-filter_complex',graph,'-map','[v]','-map','[a]',
             '-c:v','libx264','-threads','2','-preset','veryfast','-crf','20','-r','30','-pix_fmt','yuv420p',
             '-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart',destination],360)
        output=probe(destination);v=next(s for s in output['streams'] if s['codec_type']=='video');aud=next(s for s in output['streams'] if s['codec_type']=='audio')
        actual=float(output['format']['duration'])
        if (v['width'],v['height'],v['codec_name'],v['avg_frame_rate'],aud['codec_name'])!=(1080,1920,'h264','30/1','aac'):
            raise RuntimeError('Output codec, resolution, audio or frame-rate check failed.')
        if abs(actual-duration)>.15:raise RuntimeError('Export duration check failed.')
        run(['ffmpeg','-v','error','-i',destination,'-f','null','-'],120)
        record={'id':cid,'title':row['title'],'file':name,'source_start':a,'source_end':b,
            'source_start_timestamp':timecode(a),'source_end_timestamp':timecode(b),'duration':actual,
            'transcription':False,'dialogue_subtitles':False,'hook_seconds':3.5,'original_facecam':True,
            'resolution':'1080x1920','fps':30,'video_codec':'h264','audio_codec':'aac'}
        results.append(record);print('VERIFIED_EXPORT',json.dumps(record),flush=True)
    (export/'shorts_manifest.json').write_text(json.dumps({'source_url':manifest['source_url'],'selection':manifest['selection'],'clips':results},ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(export/'All_3_YouTube_Shorts.zip','w',compression=zipfile.ZIP_STORED) as archive:
        for row in results:archive.write(export/row['file'],row['file'])
    total=sum(f.stat().st_size for f in export.iterdir())
    if total>150*1024**2:raise RuntimeError('Final artifact exceeds 150 MiB safety cap.')
    print('DONE: three rendered MP4 files and their ZIP. No transcription was performed.',flush=True)


if __name__=='__main__':
    main()
