# Surrender Index 

### Andrew Shackelford 
### andrewshackelford97@gmail.com

This project contains a Twitter bot that automatically tweets every time there is a punt in an NFL game. It tweets each punt's "Surrender Index" - a completely arbitrary metric created by SB Nation's [Jon Bois](https://twitter.com/jon_bois) to quantify how cowardly a punt is.

The bots are `@surrender_index` for the main account, and `@surrender_idx90` for the secondary account that tweets only the worst punts. There is also a bot `@CancelSurrender` which allows the public to vote to "cancel" a punt's Surrender Index.

This project also contains a Jupyter Notebook used to calculate the Surrender Index for every punt since 1999, used by the bot to give context on each punt.

I don't anticipate updating this much, but I'm open to any comments or suggestions on how to improve the bot, and will work on them in my spare time (and am open to any pull requests!).  

This bot would not be possible without [nflverse's](https://github.com/nflverse) data [repository](https://github.com/nflverse/nflfastR-data) of play-by-play data for every play since 1999. Thanks to [Ben Baldwin](https://twitter.com/benbbaldwin) et al. who maintain nflfastR and its data, and check out Ben's [4th down decision bot](https://twitter.com/ben_bot_baldwin) which also analyzes 4th down plays, albeit with a bit more statistical rigor than the Surrender Index.
