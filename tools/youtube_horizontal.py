"""Free standard-runner FFmpeg export. Private source links are supplied only by Actions secret.
The zlib-packed self-contained editor is written to render_engine.py and included in the output.
All source clips are kept; no publishing action or paid computing service is used.
"""
import base64
import concurrent.futures
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import zlib

CORE = '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'


def main():
    value = os.environ.pop('VIDEO_EDIT_BUNDLE', '')
    if not value:
        raise RuntimeError('Missing private VIDEO_EDIT_BUNDLE_1080P Actions secret')
    data = json.loads(gzip.decompress(base64.b64decode(value)))
    assert len(data['sources']) == len(data['plan']) == 12
    assert sorted(x['index'] for x in data['sources']) == list(range(1, 13))
    root = Path(os.environ.get('RUNNER_TEMP', '.')) / 'youtube-edit-1080p'
    for name in ('sources', 'project', 'renders'):
        (root / name).mkdir(parents=True, exist_ok=True)
    os.environ['VIDEO_EDIT_ROOT'] = str(root)
    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise RuntimeError('Full FFmpeg and ffprobe are required')
    (root / 'runtime.json').write_text(json.dumps({'rootbase': str(root), 'ffmpeg': ffmpeg, 'ffprobe': ffprobe}))
    (root / 'edit_plan.json').write_text(json.dumps(data['plan']))
    engine = root / 'project' / 'render_engine.py'
    engine.write_bytes(zlib.decompress(base64.b64decode(CORE)))
    compile(engine.read_text(), str(engine), 'exec')
    for source in data['sources']:
        print('::add-mask::' + source['url'], flush=True)

    def download(source):
        path = root / 'sources' / ('%02d.mp4' % source['index'])
        request = urllib.request.Request(source['url'], headers={'User-Agent': 'Authorized-Video-Editor/1.0'})
        with urllib.request.urlopen(request, timeout=120) as response, path.open('wb') as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        if path.stat().st_size != source['size']:
            raise RuntimeError('Incomplete source clip %s' % source['index'])
        print('Downloaded clip %02d / 12' % source['index'], flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(download, data['sources']))
    del data['sources']
    print('All 12 original source clips verified; beginning FFmpeg render', flush=True)
    subprocess.run([sys.executable, '-u', str(engine)], check=True)
    complete = json.loads((root / 'RENDER_COMPLETE.json').read_text())
    assert complete['clips'] == 12
    final = Path(complete['path'])
    info = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(final)]))
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
    assert (video['width'], video['height'], video['codec_name']) == (1920, 1080, 'h264')
    assert audio['codec_name'] == 'aac' and audio['sample_rate'] == '48000'
    expected = sum(m['output_duration'] for m in data['plan'])
    assert abs(float(info['format']['duration']) - expected) < 0.3
    subprocess.run([ffmpeg, '-v', 'error', '-threads', '2', '-i', str(final), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], check=True, timeout=180)
    export = Path('exports')
    export.mkdir(exist_ok=True)
    shutil.copy2(final, export / final.name)
    for name in ('edit_manifest.json', 'subtitles.srt'):
        shutil.copy2(root / 'project' / name, export / name)
    shutil.copy2(engine, export / engine.name)
    report = {'clips_included': 12, 'source_clips_skipped': 0, 'width': 1920, 'height': 1080, 'fps': video.get('avg_frame_rate'), 'duration_seconds': float(info['format']['duration']), 'audio_codec': audio['codec_name'], 'audio_sample_rate': audio['sample_rate'], 'caption_cues': complete['captions'], 'decode_validation': 'passed', 'paid_services_used': False, 'audio_target_lufs': -16, 'audio_target_true_peak_dbtp': -1.5}
    (export / 'quality_report.json').write_text(json.dumps(report, indent=2))
    (export / 'README.txt').write_text('All 12 supplied clips included. 1080p horizontal FFmpeg edit with centered source picture, enlarged blurred background, 11 dip transitions with stereo whooshes, 12 original generated comedy/reaction SFX, reaction zooms, 4 slow-motion accents and 4 freeze frames. Selective automatic English reaction captions only; uncertain names and unclear dialogue omitted. Existing source overlays/watermarks preserved. Nothing published to YouTube. No paid computing service used. Sound timing follows video-shotcraft guidance; edit timing was prepared using ClipKaro and SharkyClips MIT-licensed helpers. Original media rights remain with their respective owners. Review before public posting.\n')
    print('COMPLETED: GTA_Reactions_1080p_Final.mp4 - all 12 clips', flush=True)


if __name__ == '__main__':
    main()
