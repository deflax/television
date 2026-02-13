package net.deflax.tv

import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import androidx.activity.ComponentActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.recyclerview.widget.LinearLayoutManager
import kotlin.math.pow
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import net.deflax.tv.databinding.ActivityMainBinding
import net.deflax.tv.epg.EpgAdapter
import net.deflax.tv.epg.EpgProgram
import net.deflax.tv.epg.EpgRepository
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

class MainActivity : ComponentActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var player: ExoPlayer
    private val epgAdapter = EpgAdapter()
    private val epgRepository = EpgRepository()

    private var latestPrograms: List<EpgProgram> = emptyList()
    private var lastEpgSuccess: Instant? = null
    private var overlayVisible = true
    private var retryAttempt = 0
    private var retryJob: Job? = null

    private val timeFormatter = DateTimeFormatter.ofPattern("EEE HH:mm", Locale.getDefault())
        .withZone(ZoneId.systemDefault())
    private val statusFormatter = DateTimeFormatter.ofPattern("HH:mm:ss", Locale.getDefault())
        .withZone(ZoneId.systemDefault())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupPlayer()
        setupEpg()
        startEpgPolling()
        startUiTicker()
    }

    private fun setupPlayer() {
        player = ExoPlayer.Builder(this).build()
        binding.playerView.player = player
        player.addListener(
            object : Player.Listener {
                override fun onPlaybackStateChanged(playbackState: Int) {
                    if (playbackState == Player.STATE_READY) {
                        retryJob?.cancel()
                        retryAttempt = 0
                        binding.playbackStatusText.text = getString(R.string.playback_live)
                    }
                }

                override fun onPlayerError(error: PlaybackException) {
                    schedulePlaybackRetry()
                }
            }
        )

        startPlayback()
    }

    private fun setupEpg() {
        binding.epgRecycler.layoutManager = LinearLayoutManager(this)
        binding.epgRecycler.adapter = epgAdapter
        binding.epgStatusText.text = getString(R.string.epg_loading)
        binding.playbackStatusText.text = getString(R.string.playback_live)
        binding.nowPlayingTitle.text = getString(R.string.now_playing_unknown)
        binding.nowPlayingTime.text = ""
        binding.nextProgramText.text = getString(R.string.next_program_none)
    }

    private fun startEpgPolling() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (isActive) {
                    refreshEpg()
                    delay(AppConfig.EPG_REFRESH_INTERVAL_MS)
                }
            }
        }
    }

    private fun startUiTicker() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (isActive) {
                    val now = Instant.now()
                    epgAdapter.now = now
                    if (epgAdapter.itemCount > 0) {
                        epgAdapter.notifyDataSetChanged()
                    }
                    updateNowAndNext(latestPrograms, now)
                    delay(1_000)
                }
            }
        }
    }

    private suspend fun refreshEpg() {
        runCatching { epgRepository.loadPrograms() }
            .onSuccess { programs ->
                val now = Instant.now()
                latestPrograms = programs
                lastEpgSuccess = now
                val visiblePrograms = visiblePrograms(programs, now)

                epgAdapter.now = now
                epgAdapter.submitList(visiblePrograms)

                updateNowAndNext(programs, now)
                binding.epgStatusText.text = if (visiblePrograms.isEmpty()) {
                    getString(R.string.epg_empty)
                } else {
                    getString(R.string.epg_updated, statusFormatter.format(now))
                }
            }
            .onFailure {
                val lastSuccess = lastEpgSuccess
                binding.epgStatusText.text = if (lastSuccess != null) {
                    getString(R.string.epg_stale, statusFormatter.format(lastSuccess))
                } else {
                    getString(R.string.epg_unavailable)
                }
            }
    }

    private fun visiblePrograms(programs: List<EpgProgram>, now: Instant): List<EpgProgram> {
        return programs
            .filter { it.stop.isAfter(now.minusSeconds(30)) }
            .take(20)
    }

    private fun updateNowAndNext(programs: List<EpgProgram>, now: Instant) {
        val current = programs.firstOrNull { !it.start.isAfter(now) && it.stop.isAfter(now) }
        val upcoming = programs.firstOrNull { it.start.isAfter(now) }

        if (current != null) {
            binding.nowPlayingTitle.text = current.title
            binding.nowPlayingTime.text = getString(
                R.string.epg_time_window,
                timeFormatter.format(current.start),
                timeFormatter.format(current.stop)
            )
            binding.nextProgramText.text = if (upcoming != null) {
                getString(
                    R.string.next_program_countdown,
                    upcoming.title,
                    formatCountdown(Duration.between(now, upcoming.start).seconds)
                )
            } else {
                getString(R.string.next_program_none)
            }
            return
        }

        binding.nowPlayingTitle.text = getString(R.string.now_playing_unknown)
        binding.nowPlayingTime.text = ""
        binding.nextProgramText.text = if (upcoming != null) {
            getString(
                R.string.next_program_starts_in,
                formatCountdown(Duration.between(now, upcoming.start).seconds),
                upcoming.title
            )
        } else {
            getString(R.string.next_program_none)
        }
    }

    private fun formatCountdown(secondsRaw: Long): String {
        val seconds = secondsRaw.coerceAtLeast(0)
        val hours = seconds / 3600
        val minutes = (seconds % 3600) / 60
        val remainingSeconds = seconds % 60

        return when {
            hours > 0 -> String.format(Locale.getDefault(), "%dh %02dm", hours, minutes)
            minutes > 0 -> String.format(Locale.getDefault(), "%dm %02ds", minutes, remainingSeconds)
            else -> String.format(Locale.getDefault(), "%ds", remainingSeconds)
        }
    }

    private fun startPlayback() {
        player.setMediaItem(MediaItem.fromUri(AppConfig.STREAM_URL))
        player.playWhenReady = true
        player.prepare()
    }

    private fun schedulePlaybackRetry() {
        if (retryJob?.isActive == true) {
            return
        }

        val delaySeconds = 2.0.pow(retryAttempt.toDouble()).toLong().coerceIn(1L, 30L)
        retryAttempt = (retryAttempt + 1).coerceAtMost(12)

        retryJob = lifecycleScope.launch {
            for (remaining in delaySeconds downTo 1L) {
                binding.playbackStatusText.text = getString(R.string.playback_retrying_in, remaining)
                delay(1_000)
            }
            binding.playbackStatusText.text = getString(R.string.playback_reconnecting)
            startPlayback()
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        return when (keyCode) {
            KeyEvent.KEYCODE_DPAD_CENTER,
            KeyEvent.KEYCODE_ENTER,
            KeyEvent.KEYCODE_BUTTON_SELECT,
            KeyEvent.KEYCODE_INFO,
            KeyEvent.KEYCODE_MENU -> {
                setOverlayVisible(!overlayVisible)
                true
            }

            KeyEvent.KEYCODE_BACK -> {
                if (!overlayVisible) {
                    setOverlayVisible(true)
                    true
                } else {
                    super.onKeyDown(keyCode, event)
                }
            }

            else -> super.onKeyDown(keyCode, event)
        }
    }

    private fun setOverlayVisible(visible: Boolean) {
        overlayVisible = visible
        val state = if (visible) View.VISIBLE else View.GONE
        binding.topInfoPanel.visibility = state
        binding.epgPanel.visibility = state
    }

    override fun onDestroy() {
        retryJob?.cancel()
        binding.playerView.player = null
        player.release()
        super.onDestroy()
    }
}
