import unittest
from pathlib import Path


PLAYER_JS = Path(__file__).resolve().parents[1] / 'static' / 'js' / 'player.js'


class PlayerAudioSourceTest(unittest.TestCase):
    def test_audio_only_mode_uses_audio_hls_endpoint(self):
        source = PLAYER_JS.read_text(encoding='utf-8')

        self.assertIn("const hlsSource = '/live/stream.m3u8';", source)
        self.assertIn("const audioHlsSource = '/live/audio.m3u8';", source)
        self.assertIn('window.StreamApp.hlsSource = hlsSource;', source)
        self.assertIn('window.hls.loadSource(hlsSource);', source)
        self.assertIn('if (shouldRestoreAudioOnly) {', source)
        self.assertIn('video.src = hlsSource;', source)
        self.assertIn('audioHls.loadSource(audioHlsSource);', source)
        self.assertIn('audioEl.src = audioHlsSource;', source)
        self.assertIn('audioEl.controls = true;', source)
        self.assertIn("audioEl.className = 'w-100 mt-2';", source)
        self.assertIn('(streamMedia || document.body).appendChild(audioEl);', source)
        self.assertIn("audioEl.style.display = 'block';", source)
        self.assertIn('window.StreamApp.hlsSource = audioHlsSource;', source)
        self.assertIn('window.StreamApp.hlsSource = hlsSource;', source)
        self.assertIn('window.hls.detachMedia();', source)
        self.assertIn("video.removeAttribute('src');", source)
        self.assertIn("video.src = '';", source)
        self.assertIn('window.hls.attachMedia(video);', source)


if __name__ == '__main__':
    _ = unittest.main()
