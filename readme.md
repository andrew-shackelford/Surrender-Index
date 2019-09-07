# Surrender Index 

### Andrew Shackelford 
### ashackelford@college.harvard.edu

This project contains a Twitter bot that automatically tweets every time there is a punt in an NFL game. It tweets each punt's "Surrender Index" - a completely arbitrary metric created by SB Nation's [Jon Bois](https://twitter.com/jon_bois) to quantify how cowardly a punt is.

_The bots are `@surrender_index` for the main account, and `@surrender_idx90` for the secondary account._

This project also contains a Jupyter Notebook used to calculate the Surrender Index for every punt since 2009, used by the bot to give context on each punt.

I don't anticipate updating this much, but I'm open to any comments or suggestions on how to improve the bot, and will work on them in my spare time (and am open to any pull requests!).  

I'd also be interested if anyone has an free data source that goes back before 2009 and doesn't involve manually scraping Pro Football Reference.

This bot would not be possible without two critical resources:  
* [Andrew Gallant](https://github.com/BurntSushi) and [Derek Adair's](https://github.com/derek-adair) [nflgame](https://github.com/derek-adair/nflgame), which provides live NFL stats with an easy-to-use API.  
* [Ron Yurko's](https://github.com/ryurko) data [repository](https://github.com/ryurko/nflscrapR-data) of play-by-play data for every play since 2009.