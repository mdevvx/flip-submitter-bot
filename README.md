## 🧠 Bot Commands

### 🎯 User Commands

#### `/flip`

> Submit a new flip for admin approval.
> Opens a modal to enter:

* **Item Name**
* **Purchase Price**
* **Parts Price**
* **Sales Price**

✅ Calculates your total profit automatically and sends the flip for review.

---

### ⚙️ Admin Commands

#### `/setchannels`

> Configure the bot’s channels for your server.
> **Options:**

* `member_flips_channel` → Where user flips are posted for approval.
* `leaderboard_channel` → Where the leaderboard is displayed.

#### `/setlogchannel`

> Set the log channel where bot activities (approvals, denials, errors) are recorded.
> Useful for moderation and audit tracking.

#### `/pingdb`

> Tests the connection between the bot and Supabase database.
> Useful for troubleshooting database connectivity.

#### `/sync`

> Sync all slash commands with Discord.

#### `/showconfig`

> View all selected channels and configuration details.
> Shows:

* Member flips channel
* Leaderboard channel
* Log channel (if any)

---

### 🏆 Auto Features

* Automatically **updates leaderboard** when a flip is approved.
* Logs approved and denied flips in the configured log channel.
* Keeps guild-specific settings saved in Supabase.
