package net.deflax.tv.epg

import org.xmlpull.v1.XmlPullParser
import org.xmlpull.v1.XmlPullParserFactory
import java.io.StringReader
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

class EpgXmlParser {
    fun parse(xml: String): List<EpgProgram> {
        val parser = XmlPullParserFactory.newInstance().newPullParser().apply {
            setInput(StringReader(xml))
        }

        val programs = mutableListOf<EpgProgram>()
        var eventType = parser.eventType
        var current: MutableProgram? = null
        var currentTextTag: String? = null

        while (eventType != XmlPullParser.END_DOCUMENT) {
            when (eventType) {
                XmlPullParser.START_TAG -> {
                    when (parser.name) {
                        "programme" -> {
                            current = MutableProgram(
                                channelId = parser.getAttributeValue(null, "channel").orEmpty(),
                                startRaw = parser.getAttributeValue(null, "start").orEmpty(),
                                stopRaw = parser.getAttributeValue(null, "stop").orEmpty()
                            )
                        }

                        "title", "desc" -> currentTextTag = parser.name
                        else -> currentTextTag = null
                    }
                }

                XmlPullParser.TEXT -> {
                    val text = parser.text?.trim().orEmpty()
                    if (text.isNotEmpty() && current != null) {
                        when (currentTextTag) {
                            "title" -> if (current.title.isBlank()) current.title = text
                            "desc" -> if (current.description.isBlank()) current.description = text
                        }
                    }
                }

                XmlPullParser.END_TAG -> {
                    if (parser.name == "programme") {
                        current?.toProgram()?.let(programs::add)
                        current = null
                    }
                    currentTextTag = null
                }
            }
            eventType = parser.next()
        }

        return programs
    }

    private fun parseXmltvTimestamp(raw: String): Instant? {
        val parts = raw.trim().split(Regex("\\s+"))
        if (parts.isEmpty() || parts.first().isBlank()) {
            return null
        }

        val dateToken = parts[0]
        val localDateTime = when (dateToken.length) {
            14 -> LocalDateTime.parse(dateToken, DATE_TIME_SECONDS)
            12 -> LocalDateTime.parse(dateToken, DATE_TIME_MINUTES)
            8 -> LocalDate.parse(dateToken, DATE_ONLY).atStartOfDay()
            else -> return null
        }

        val zoneOffset = parseZoneOffset(parts.getOrNull(1)) ?: ZoneOffset.UTC
        return localDateTime.toInstant(zoneOffset)
    }

    private fun parseZoneOffset(raw: String?): ZoneOffset? {
        if (raw.isNullOrBlank()) {
            return null
        }

        val normalized = raw.trim().replace(Regex("^([+-]\\d{2})(\\d{2})$"), "$1:$2")
        return runCatching { ZoneOffset.of(normalized) }.getOrNull()
    }

    private inner class MutableProgram(
        private val channelId: String,
        private val startRaw: String,
        private val stopRaw: String,
        var title: String = "",
        var description: String = ""
    ) {
        fun toProgram(): EpgProgram? {
            val start = parseXmltvTimestamp(startRaw) ?: return null
            val stop = parseXmltvTimestamp(stopRaw) ?: return null
            if (!stop.isAfter(start)) {
                return null
            }

            return EpgProgram(
                channelId = channelId,
                title = title.ifBlank { "Untitled" },
                description = description.ifBlank { null },
                start = start,
                stop = stop
            )
        }
    }

    private companion object {
        val DATE_TIME_SECONDS: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyyMMddHHmmss")
        val DATE_TIME_MINUTES: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyyMMddHHmm")
        val DATE_ONLY: DateTimeFormatter = DateTimeFormatter.BASIC_ISO_DATE
    }
}
