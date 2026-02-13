package net.deflax.tv.epg

import java.time.Instant

data class EpgProgram(
    val channelId: String,
    val title: String,
    val description: String?,
    val start: Instant,
    val stop: Instant
)
