package net.deflax.tv.epg

import net.deflax.tv.AppConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException

class EpgRepository(
    private val epgUrl: String = AppConfig.EPG_URL,
    private val channelId: String = AppConfig.EPG_CHANNEL_ID,
    private val client: OkHttpClient = OkHttpClient(),
    private val parser: EpgXmlParser = EpgXmlParser()
) {
    suspend fun loadPrograms(): List<EpgProgram> = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(epgUrl)
            .get()
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IOException("EPG request failed with code ${response.code}")
            }

            val xml = response.body?.string().orEmpty()
            if (xml.isBlank()) {
                return@withContext emptyList()
            }

            val allPrograms = parser.parse(xml).sortedBy { it.start }
            if (channelId.isBlank()) {
                allPrograms
            } else {
                allPrograms.filter { it.channelId == channelId }
            }
        }
    }
}
