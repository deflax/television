package net.deflax.tv.epg

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import net.deflax.tv.R
import net.deflax.tv.databinding.ItemEpgEventBinding
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

class EpgAdapter : ListAdapter<EpgProgram, EpgAdapter.EpgViewHolder>(DiffCallback) {
    var now: Instant = Instant.now()

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): EpgViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        val binding = ItemEpgEventBinding.inflate(inflater, parent, false)
        return EpgViewHolder(binding)
    }

    override fun onBindViewHolder(holder: EpgViewHolder, position: Int) {
        holder.bind(getItem(position), now)
    }

    class EpgViewHolder(
        private val binding: ItemEpgEventBinding
    ) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: EpgProgram, now: Instant) {
            val formatter = DateTimeFormatter.ofPattern("HH:mm", Locale.getDefault())
                .withZone(ZoneId.systemDefault())
            val start = formatter.format(item.start)
            val stop = formatter.format(item.stop)
            val isLive = !item.start.isAfter(now) && item.stop.isAfter(now)

            binding.itemTime.text = binding.root.context.getString(R.string.epg_time_window, start, stop)
            binding.itemTitle.text = item.title
            binding.itemDescription.text = item.description.orEmpty()
            binding.livePill.text = if (isLive) binding.root.context.getString(R.string.live_now) else ""
            binding.livePill.visibility = if (isLive) View.VISIBLE else View.GONE
            binding.root.setBackgroundResource(
                if (isLive) R.drawable.item_live_background else android.R.color.transparent
            )
        }
    }

    private companion object {
        val DiffCallback = object : DiffUtil.ItemCallback<EpgProgram>() {
            override fun areItemsTheSame(oldItem: EpgProgram, newItem: EpgProgram): Boolean {
                return oldItem.channelId == newItem.channelId &&
                    oldItem.start == newItem.start &&
                    oldItem.stop == newItem.stop
            }

            override fun areContentsTheSame(oldItem: EpgProgram, newItem: EpgProgram): Boolean {
                return oldItem == newItem
            }
        }
    }
}
