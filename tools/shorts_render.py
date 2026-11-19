"""Manual, local/CPU Shorts workflow. Never posts or publishes videos.

Analysis creates review assets, NOT final Shorts. Rendering requires explicit,
reviewed cuts and caption text. No paid API, browser cookies or account secrets.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import wave
import zipfile


def run(cmd, timeout=1200):
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError('Command failed: ' + str(cmd[0]) + '\n' + p.stderr[-5000:])
    return p.stdout


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def probe_file(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path]))


def ytdlp_args():
    return [sys.executable, '-m', 'yt_dlp', '--no-playlist', '--js-runtimes', 'node',
            '--socket-timeout', '20', '--retries', '1', '--extractor-retries', '1']


def source_probe(url, work):
    data = json.loads(run(ytdlp_args() + ['--skip-download', '--dump-single-json', url], 150))
    save(work / 'private_source_info.json', data)
    public = {k: data.get(k) for k in ['id', 'title', 'duration', 'live_status', 'channel', 'webpage_url', 'release_timestamp']}
    if not data.get('formats'):
        raise RuntimeError('Source has no accessible video/audio formats. Supply a completed source file.')
    save(work / 'artifacts/source_metadata.json', public)
    print('Source availability confirmed:', json.dumps(public, ensure_ascii=False), flush=True)
    return data


def download_interval(url, start, end, destination, height=1080, exact=False):
    destination = Path(destination)
    if destination.exists() and destination.stat().st_size > 10000:
        return destination
    fmt = f'bv*[vcodec^=avc1][height<={height}]+ba[acodec^=mp4a]/bv*[height<={height}]+ba/b[height<={height}]'
    cmd = ytdlp_args() + ['-f', fmt, '--merge-output-format', 'mp4', '--remux-video', 'mp4',
        '--concurrent-fragments', '4', '--fragment-retries', '2', '--abort-on-unavailable-fragments',
        '--download-sections', f'*{start:.3f}-{end:.3f}', '-o', str(destination.with_suffix('')) + '.%(ext)s']
    if exact:
        cmd += ['--force-keyframes-at-cuts', '--postprocessor-args', 'ffmpeg:-preset fast']
    cmd += [url]
    run(cmd, 900)
    if not destination.exists():
        raise RuntimeError('Downloader did not produce the expected MP4. No replacement footage was generated.')
    probe_file(destination)
    return destination


def collect_chat(url, start, end, output):
    result = []
    error = None
    try:
        from chat_downloader import ChatDownloader
        downloader = ChatDownloader()
        chat = downloader.get_chat(url, start_time=start, end_time=end, max_messages=3000,
                                   timeout=40, inactivity_timeout=8)
        seen = set()
        for item in chat:
            t = item.get('time_in_seconds')
            text = str(item.get('message') or '').strip()
            user = str((item.get('author') or {}).get('id') or '')
            kind = str(item.get('message_type') or '')
            if t is None or not start <= float(t) <= end or not text:
                continue
            if any(x in kind for x in ['membership', 'gift', 'sticker']):
                continue
            key = (user, text.casefold())
            if key in seen:
                continue
            seen.add(key)
            result.append({'time': float(t), 'text': text, 'user': user})
        downloader.close()
    except Exception as exc:
        error = str(exc)[:1200]
    save(output, {'messages': result, 'error': error,
                  'note': 'Viewer reactions are hints, not proof. Gifts and repeated same-user spam are excluded.'})
    return result


def face_box(image):
    import cv2
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    found = detector.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(22, 22))
    return found.tolist() if len(found) else []


def visual_review(source, out, duration):
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    cv2.setNumThreads(1)
    cap = cv2.VideoCapture(str(source))
    rows = []
    thumbnails = []
    for t in range(0, max(1, int(duration)), 10):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        thumb = cv2.resize(frame, (640, 360))
        boxes = face_box(thumb)
        rows.append({'time': t, 'faces_640x360': boxes})
        if t % 20 == 0:
            rgb = cv2.cvtColor(cv2.resize(frame, (480, 270)), cv2.COLOR_BGR2RGB)
            tile = Image.new('RGB', (480, 302), (15, 18, 24))
            tile.paste(Image.fromarray(rgb), (0, 30))
            ImageDraw.Draw(tile).text((12, 8), f'Source window +{t // 60:02}:{t % 60:02}', fill='white')
            thumbnails.append(tile)
    cap.release()
    sheet = Image.new('RGB', (1920, max(1, math.ceil(len(thumbnails) / 4)) * 302), (15, 18, 24))
    for i, tile in enumerate(thumbnails):
        sheet.paste(tile, ((i % 4) * 480, (i // 4) * 302))
    sheet.save(out / 'contact_sheet.jpg', quality=82)
    save(out / 'visual_samples.json', rows)
    return rows


def transcribe(audio, out):
    import numpy as np
    from faster_whisper import WhisperModel
    with wave.open(str(audio), 'rb') as wav:
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel('small.en', device='cpu', compute_type='int8', cpu_threads=2, num_workers=1)
    segments, info = model.transcribe(pcm, language='en', beam_size=3, word_timestamps=True,
        condition_on_previous_text=False, vad_filter=True, temperature=0,
        vad_parameters={'min_silence_duration_ms': 400})
    rows = []
    with open(out / 'transcript.jsonl', 'w', encoding='utf-8') as stream:
        for s in segments:
            row = {'start': s.start, 'end': s.end, 'text': s.text.strip(),
                'avg_logprob': s.avg_logprob, 'no_speech_prob': s.no_speech_prob,
                'words': [{'start': w.start, 'end': w.end, 'word': w.word, 'probability': w.probability}
                          for w in (s.words or [])]}
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            print(f'[{s.start:.1f}-{s.end:.1f}] {s.text}', flush=True)
    save(out / 'transcript.json', rows)
    return rows, pcm


def candidates(transcript, pcm, chat, absolute_start, duration):
    import numpy as np
    reaction = re.compile(r'\b(what|wait|no way|oh my|hell|bro|crazy|why|stop|look|damn|sleep|crash)\b|😂|🤣|💀|😭', re.I)
    starts = sorted(set(max(0.0, min(duration - 30, float(s['start']) - 3)) for s in transcript))
    scored = []
    for start in starts:
        end = min(duration, start + 30)
        spoken = [s for s in transcript if s['end'] > start and s['start'] < end]
        text = ' '.join(s['text'] for s in spoken)
        samples = pcm[int(start * 16000):int(end * 16000)]
        hops = [float(np.sqrt(np.mean(samples[i:i + 4000] ** 2) + 1e-10))
                for i in range(0, max(1, len(samples) - 4000), 4000)]
        energy = max(hops or [0])
        variation = float(np.std(hops)) if hops else 0
        chats = [m for m in chat if start + absolute_start <= m['time'] < end + absolute_start + 8]
        users = len(set(m['user'] for m in chats))
        reactions = sum(bool(reaction.search(m['text'])) for m in chats)
        score = min(4, energy * 12) + min(3, variation * 25) + min(2, len(reaction.findall(text)) * .22)
        score += min(2, users / 15) + min(2, reactions / 6)
        if len(text.split()) < 12:
            score -= 1.5
        scored.append({'start': round(start, 3), 'end': round(end, 3), 'score': round(score, 3),
                       'transcript': text, 'chat_users': users, 'chat_reactions': reactions})
    picked = []
    for row in sorted(scored, key=lambda r: r['score'], reverse=True):
        if all(row['end'] <= r['start'] - 3 or row['start'] >= r['end'] + 3 for r in picked):
            picked.append(row)
        if len(picked) == 6:
            break
    return picked


def analyze(url, requested_start, work):
    out = work / 'artifacts'
    info = json.loads((work / 'private_source_info.json').read_text())
    full_duration = float(info.get('duration') or 0)
    if full_duration <= 0:
        raise RuntimeError('Source is still live without a fixed duration. Supply a completed source recording.')
    start = max(0, full_duration - 930) if requested_start < 0 else requested_start
    end = min(full_duration - 1, start + 900)
    if end - start < 90:
        raise RuntimeError('Requested source window is too short to choose three different moments.')
    print(f'Analyzing only fixed source window {start:.3f}-{end:.3f}, not the entire long broadcast.', flush=True)
    source = download_interval(url, start, end, work / 'source_window.mp4', height=1080)
    details = probe_file(source)
    duration = min(end - start, float(details['format']['duration']))
    save(out / 'window.json', {'source_url': url, 'source_start': start, 'source_end': start + duration,
         'duration': duration, 'scope': 'Fixed approximately 15-minute window, not the entire broadcast'})
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        chat_future = pool.submit(collect_chat, url, start, start + duration, out / 'chat_reactions.json')
        run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', source,
             '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', work / 'audio.wav'])
        visual_review(source, out, duration)
        transcript, pcm = transcribe(work / 'audio.wav', out)
        chat = chat_future.result(timeout=60)
    selected = candidates(transcript, pcm, chat, start, duration)
    if len(selected) < 3:
        raise RuntimeError('Fewer than three distinct candidates were found. No final clips were fabricated.')
    for i, row in enumerate(selected, 1):
        row['id'] = f'candidate_{i:02}'
        row['asset_start'] = max(0, row['start'] - 4)
        row['asset_end'] = min(duration, row['end'] + 4)
        row['absolute_start'] = start + row['start']
        row['absolute_end'] = start + row['end']
        row['asset_source_start'] = start + row['asset_start']
        row['asset'] = row['id'] + '.mp4'
        run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', row['asset_start'],
             '-i', source, '-t', row['asset_end'] - row['asset_start'], '-vf', 'scale=1280:-2,fps=30',
             '-c:v', 'libx264', '-preset', 'fast', '-crf', '22', '-maxrate', '2500k', '-bufsize', '5000k',
             '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', out / row['asset']], 180)
    save(out / 'candidates.json', selected)
    size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
    if size > 120 * 1024 ** 2:
        raise RuntimeError('Analysis artifact exceeds the 120 MiB safety cap.')
    save(out / 'analysis_complete.json', {'final_videos_rendered': 0, 'candidate_count': len(selected),
         'artifact_bytes': size, 'requires': 'Human/assistant review, exact cuts, faithful captions and facecam layout'})
    print('Analysis finished. These are review candidates, not finished Shorts.', flush=True)


def ass_time(t):
    cs = int(round(float(t) * 100))
    return f'{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}'


def ass_escape(text):
    return str(text).replace('\\', '').replace('{', '').replace('}', '').replace('\n', r'\N')


def make_ass(path, clip, duration):
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Caption,DejaVu Sans,68,&H00FFFFFF,&H0000FFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,5,1,5,80,170,0,1
Style: Hook,DejaVu Sans,76,&H0000FFFF,&H0000FFFF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,6,2,8,75,160,150,1
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
'''
    events = []
    hook = ass_escape(clip['hook'])
    events.append(f'Dialogue: 1,0:00:00.00,{ass_time(min(3.5, duration))},Hook,,0,0,0,,{{\\fad(80,120)}}{hook}')
    for caption in clip['captions']:
        a, b = float(caption['start']), float(caption['end'])
        if not 0 <= a < b <= duration + .06:
            raise ValueError('Caption timing is outside the reviewed clip.')
        text = ass_escape(caption['text'])
        for emphasis in caption.get('highlight', []):
            clean = ass_escape(emphasis)
            text = re.sub(r'(?i)(?<!\w)' + re.escape(clean) + r'(?!\w)',
                          lambda m: r'{\c&H0000FFFF&}' + m.group(0) + r'{\c&H00FFFFFF&}', text)
        text = r'{\an5\pos(510,1000)\fad(35,45)}' + text
        events.append(f'Dialogue: 2,{ass_time(a)},{ass_time(b)},Caption,,0,0,0,,{text}')
    path.write_text(header + '\n'.join(events) + '\n', encoding='utf-8')


def choose_facecam(source, requested=None):
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    meta = probe_file(source)
    v = next(s for s in meta['streams'] if s['codec_type'] == 'video')
    width, height = int(v['width']), int(v['height'])
    if requested:
        x, y, w, h = [float(x) for x in requested]
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.001 and y + h <= 1.001):
            raise ValueError('Invalid normalized facecam rectangle.')
        return [int(x * width) // 2 * 2, int(y * height) // 2 * 2,
                max(2, int(w * width) // 2 * 2), max(2, int(h * height) // 2 * 2)]
    cap = cv2.VideoCapture(str(source))
    duration = float(meta['format']['duration'])
    boxes = []
    for t in [1, duration * .25, duration * .5, duration * .75, max(0, duration - 1)]:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, image = cap.read()
        if not ok:
            continue
        thumb = cv2.resize(image, (640, 360))
        for box in face_box(thumb):
            x, y, w, h = box
            if x < 165 or x + w > 480:
                boxes.append(box)
    cap.release()
    if len(boxes) < 2:
        raise RuntimeError('No consistent original facecam detected. Separate facecam footage or a reviewed crop is required.')
    clusters = {}
    for box in boxes:
        key = (int((box[0] + box[2] / 2) // 70), int((box[1] + box[3] / 2) // 70))
        clusters.setdefault(key, []).append(box)
    group = max(clusters.values(), key=len)
    if len(group) < 2:
        raise RuntimeError('Face detections are not persistent enough to identify a real facecam.')
    x, y, w, h = np.median(np.array(group), axis=0)
    cw = min(640, max(120, w * 5.2)); ch = min(360, cw / 1.5)
    cx, cy = x + w / 2, y + h * .85
    left = max(0, min(640 - cw, cx - cw / 2)); top = max(0, min(360 - ch, cy - ch * .42))
    return [int(left / 640 * width) // 2 * 2, int(top / 360 * height) // 2 * 2,
            int(cw / 640 * width) // 2 * 2, int(ch / 360 * height) // 2 * 2]


def render(url, work):
    config = json.loads(os.environ.get('EDIT_JSON', '{}'))
    clips = config.get('clips') or []
    if len(clips) != 3:
        raise ValueError('Exactly three reviewed clip specifications are required.')
    restored = work / 'restored'
    candidates_file = next(restored.rglob('candidates.json'), None)
    if not candidates_file:
        raise RuntimeError('Analysis artifact is missing candidates.json.')
    base = candidates_file.parent
    pool = {x['id']: x for x in json.loads(candidates_file.read_text())}
    ranges = []
    for item in clips:
        a, b = float(item['source_start']), float(item['source_end'])
        if not 20 <= b - a <= 30.05:
            raise ValueError('Each final Short must be 20-30 seconds, including its overlaid hook.')
        if any(a < end and b > start for start, end in ranges):
            raise ValueError('Final clips cannot reuse overlapping source moments.')
        if not item.get('captions') or not item.get('hook') or not item.get('title'):
            raise ValueError('Reviewed captions, a truthful hook and a title are required.')
        ranges.append((a, b))
    export = work / 'exports'; export.mkdir(exist_ok=True)
    records = []
    for index, clip in enumerate(clips, 1):
        a, b = float(clip['source_start']), float(clip['source_end'])
        duration = b - a
        row = pool[clip['candidate_id']]
        source = work / f'original_{index}.mp4'
        quality = 'Original accessible source, precise re-cut'
        try:
            download_interval(url, a, b, source, height=1080, exact=True)
            offset = 0
        except Exception as exc:
            source = base / row['asset']
            offset = a - float(row['asset_source_start'])
            if offset < 0 or offset + duration > float(row['asset_end']) - float(row['asset_start']) + .05:
                raise RuntimeError('Original re-download failed and reviewed cut is outside its saved source asset.') from exc
            quality = 'Saved 720p source asset, upscaled for the required 1080x1920 output'
            print(f'Clip {index}: using preserved source asset; original re-download unavailable.', flush=True)
        roi = choose_facecam(source, clip.get('facecam_roi'))
        x, y, w, h = roi
        ass = work / f'captions_{index}.ass'; make_ass(ass, clip, duration)
        safe_ass = str(ass.resolve()).replace('\\', '/').replace(':', r'\:')
        graph = (
            '[0:v]fps=30,split=3[bg][main][cam];'
            '[bg]scale=1080:1060:force_original_aspect_ratio=increase,crop=1080:1060,boxblur=28:2[blur];'
            '[main]scale=1080:860:force_original_aspect_ratio=decrease[game];'
            f'[cam]crop={w}:{h}:{x}:{y},scale=1080:720:force_original_aspect_ratio=decrease,'
            'pad=1080:720:(ow-iw)/2:(oh-ih)/2:color=0x101218[face];'
            '[blur][game]overlay=(W-w)/2:(H-h)/2[top];'
            '[top]pad=1080:1920:0:0:color=0x101218[canvas];'
            '[canvas][face]overlay=0:1100[layout];'
            f"[layout]subtitles=filename='{safe_ass}',setsar=1,format=yuv420p[v]"
        )
        filename = f'Clip_{index}_Best_Moment_{index}.mp4'
        destination = export / filename
        run(['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin', '-y', '-ss', offset,
             '-i', source, '-t', duration, '-filter_complex', graph, '-map', '[v]', '-map', '0:a:0',
             '-af', 'highpass=f=65,loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.95',
             '-c:v', 'libx264', '-preset', 'fast', '-crf', '19', '-r', '30', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', destination], 600)
        meta = probe_file(destination)
        video = next(s for s in meta['streams'] if s['codec_type'] == 'video')
        audio = next(s for s in meta['streams'] if s['codec_type'] == 'audio')
        final_duration = float(meta['format']['duration'])
        if (video['width'], video['height'], video['codec_name'], video['avg_frame_rate'], audio['codec_name']) != (1080, 1920, 'h264', '30/1', 'aac'):
            raise RuntimeError('Export specification validation failed.')
        if abs(final_duration - duration) > .15:
            raise RuntimeError('Unexpected audio/video duration mismatch.')
        run(['ffmpeg', '-v', 'error', '-i', destination, '-f', 'null', '-'], 180)
        records.append({'clip': index, 'title': clip['title'], 'source_start': a, 'source_end': b,
                        'duration': final_duration, 'file': filename, 'source_quality': quality,
                        'facecam_crop_pixels': roi, 'caption_language': 'Roman-Hindi translation of reviewed English dialogue'})
        print('Verified final export:', json.dumps(records[-1], ensure_ascii=False), flush=True)
    save(export / 'shorts_manifest.json', records)
    with zipfile.ZipFile(export / 'All_3_YouTube_Shorts.zip', 'w', compression=zipfile.ZIP_STORED) as archive:
        for row in records:
            archive.write(export / row['file'], arcname=row['file'])
    if sum(f.stat().st_size for f in export.iterdir() if f.is_file()) > 150 * 1024 ** 2:
        raise RuntimeError('Final artifact exceeds the 150 MiB storage safety cap.')
    print('All three MP4s and their ZIP were actually rendered and validated.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['probe', 'analyze', 'render'])
    parser.add_argument('--url', required=True)
    parser.add_argument('--start', type=float, default=-1)
    parser.add_argument('--work', type=Path, default=Path('work'))
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    (args.work / 'artifacts').mkdir(exist_ok=True)
    try:
        if args.mode == 'probe': source_probe(args.url, args.work)
        elif args.mode == 'analyze': analyze(args.url, args.start, args.work)
        else: render(args.url, args.work)
    except Exception as exc:
        save(args.work / 'artifacts/error.json', {'stage': args.mode, 'error': str(exc)[-6000:],
             'final_shorts_ready': False, 'required_action': 'Inspect the exact error; do not bypass access restrictions or claim exports exist.'})
        raise

if __name__ == '__main__':
    main()
