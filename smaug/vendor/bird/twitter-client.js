// Trimmed for smaug-jev: only the read mixins Smaug uses (see README.md).
import { TwitterClientBase } from './twitter-client-base.js';
import { withSearch } from './twitter-client-search.js';
import { withTimelines } from './twitter-client-timelines.js';
import { withTweetDetails } from './twitter-client-tweet-detail.js';
import { withUserLookup } from './twitter-client-user-lookup.js';
import { withUserTweets } from './twitter-client-user-tweets.js';
import { withUsers } from './twitter-client-users.js';
const MixedTwitterClient = withUserTweets(withUserLookup(withUsers(withTimelines(withSearch(withTweetDetails(TwitterClientBase))))));
export class TwitterClient extends MixedTwitterClient {
}
