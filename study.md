## TODO:

1. double tweets. 
2. check for other tones

- Pagination for dashboard tables
- make tables that aahan and trisha need a bit more user friendly
- the asnwer should be to the question of 'is this true?' during invocation etc
- how are we handling videos?
- Verdict label still NEI even though the reply makes a confident refutation — Newsweek + The Source aren't in our 23-domain canned source-quality table (which only covers IFCN signatories + a handful of Wikipedia-RSP entries), so they fall through to unknown tier and don't count toward the ≥2-reliable-tier threshold. The reply text is still good because the renderer reads presentation_payload, not verdict_label. Real fix is to expand the source-quality table — defer.-Ask claude to read all or atleast the few best papers on the @grok phenomeena and compare our work and deisgnsand ersearcher and stufy design with these different studies and their findings
- someone replued to invoked tweet before the fact check came in, and thefact check shows up under the other person's reply not the invoked one
- Make the bot slightly more general such that it can find evidence to challenge views.
- is there some way that you can make it easier for the undergrads to find the 10 replies from the previous day for the surveys? Maybe some way to export to CSV?
- We should have the LLMgenerate the search queries, and not have templates. have good guidance on the search query generation
- 403 errors while extracting markdown from pages

---

We need to take into cosideration the goal the invoker gave to the bot. it could be factchecking, but it could also be challanging an opinion, getting more cintext, finding different perspectives etc. If the invoker only tags and says nothing, we will decide whats the best thing to do here ourselves: like for a factual claim we will fact check, for an opi ion we will challange, for a claim made out of context, we will provide context etc.

In stage 1.5, we also need to check if the image itself is a well known image or viral image etc. a meta recognition.

Extract + check worthiness needs to be slightly more broader, it can have things about 'adding context', deserves pushback, etc. it should also take into consideration what the invoker said and asked the bot to do.

---

We need to consolidate all the sources, reasonng, thinking etc in the info page. only a link to that in the tweet itself

## Research Question

How does the tone of an AI fact-check affect (1) engagement with the fact-check and (2) affective polarization in those exposed to the fact-check?

Hypothesis: Satirical AI fact-checks will decrease **antagonistic** affective polarization in those exposed to the fact-check but garner higher engagement than agreeable or neutral AI fact checks. Satirical fact-checks may or may not decrease **agonistic** affective polarization. (See related work section for definitions)

## Bot Details

There is a single bot, **@eddie\_bot**. All participants and bystanders interact with the same bot account, but the bot replies in one of three tones:

* Agreeable Tone ☺️
* Neutral Tone 🧐
* Satirical (“Edgy”) Tone 😎

**Tone selection per mention:**

* **Registered participants** receive replies in the tone assigned at enrolment (see Participant Task below). The tone is sticky — every mention from that participant gets the same tone, allowing within-subject tracking.
* **Unregistered users** (bystanders or anyone outside the study who happens to mention the bot) get a tone drawn uniformly at random per mention.

The three tone prompts share the same retrieval pipeline; only the final response template differs.

## Recruitment

We aim to recruit about 30 students from UW through a Google Form.

## Participant Task

We will ask participants to complete the following tasks:

* Before starting the study, participants will be asked to fill out a pre-survey.
* Participants will be assigned 1 tone (agreeable / neutral / satirical) at enrolment. They mention the same bot — @eddie\_bot — and the bot replies in their assigned tone.
* Over a 5 day period, participants will be sent a search query that finds posts about a particular topic with high engagement (e.g., posts about immigration with at least 20 replies). They will be required to identify 10 posts that they disagree with, and mention @eddie\_bot in reply to those posts.
* One day after the bot replies to a post, participants will be asked to fill out a DM survey about the quality of discussion evoked by the bot.
* Participants will be asked to fill out a post-survey at the conclusion of the study.

## 

## Metrics \- Invokers

We will ask the following survey questions of **invokers**, who are hired to invoke our bot on posts that they disagree with.

### Pre-Survey (Google Forms)

**What is Measured: Antagonistic Affective Polarization**

* It is acceptable for \[members of your political party\] to send threatening and intimidating messages to leaders \[of an opposing political party\]. (0-10, strongly disagree to neutral to strongly agree)
* It is justified for \[members of your political party\] to use violence in advancing their political goals these days. (0-10, strongly disagree to neutral to strongly agree)
    - Adapted from Stray et al. (2026)’s survey questions for “support for political violence”
* It is acceptable to manipulate political procedures to exclude \[members of an opposing political party\] from participating (0-10, strongly disagree to neutral to strongly agree)
    - Informed by Westphal (2022)’s definition of antagonistic behavior as encompassing calls to manipulate political procedures to the exclusion of outgroups.
* I am able to make change through political processes like voting (0-10, strongly disagree to neutral to strongly agree)
    - Informed by Mouffe (2000)’s argument that an excess of consensus leads people who are dissatisfied with the status quo to feel that they have no options to make change through political processes (which leads to violent extremism)

**What is Measured: Agonistic Affective Polarization**

* Please indicate how you feel toward \[members of an opposing political party\] using the scale below. (0-10, very unfavorable/cold to neutral to very favorable/warm)
* Please indicate how you feel toward \[members of your political party\] using the scale below. (0-10, very unfavorable/cold to neutral to very favorable/warm)
* How comfortable are you having friends who are \[members of an opposing political party\]? (0-10, not at all to completely)
* How comfortable are you having friends who are \[ingroup members\]? (0-10, not at all to completely)
    - Adapted from Stray et al. (2026)’s survey questions for “affective polarization”

**What is Measured: Engagement**

* I feel able to engage in fact-checking posts that I disagree with on X (0-10, strongly disagree to neutral to strongly agree)

### Daily Surveys (X DMs)

**What is Measured: Engagement**

* Give the username of the author of the post you invoked the bot on
    - Attention check to make sure participants read the post before answering the survey questions
* After reading the bot’s post, I feel interested in learning more about this topic. (0-10, strongly disagree to neutral to strongly agree)
* After reading the bot’s post, I feel interested in engaging in further discussion about this topic on X. (0-10, strongly disagree to neutral to strongly agree)
* After reading replies to the bot’s post, I feel interested in continuing to follow this discussion thread (if applicable) (0-10, strongly disagree to neutral to strongly agree)
* After reading replies to the bot’s post, I feel interested in engaging in this discussion thread (if applicable) (0-10, strongly disagree to neutral to strongly agree)

**Format:** One day after the bot replies, we will send participants a DM with links to 10 bot posts from the previous day. Each post will be associated with a three-letter code. Participants will click on a Google Form link and answer questions about each post, putting in the three-letter code for each section so we can match responses to posts.

### Post-Survey (Google Forms)

**What is Measured: Antagonistic Affective Polarization**  
Same questions as pre-survey

**What is Measured: Agonistic Affective Polarization**  
Same questions as pre-survey

**What is Measured: Engagement**  
Same questions as pre-survey, plus the following questions:

* Overall, I was satisfied with the quality of the bot’s responses (0-10, strongly disagree to neutral to strongly agree)
    - Measures quality of bot’s reply from participant’s perspective
* In 2-3 sentences, explain your answer to the previous question
* Overall, I would use this bot again (0-10, strongly disagree to neutral to strongly agree)
    - Measures engagement from the participant side
* In 2-3 sentences, explain your answer to the previous question

## 

## Metrics \- Bystanders

In addition to collecting the above metrics from invokers, we will also collect the following metrics about bot engagement from **bystanders**, or X users who are organically exposed to the bot replies. We assume that bystanders will largely consist of people who disagree with the fact-check.

**What is Measured: Engagement**

* Number of likes, reposts, and replies
    - On original post (measured at time of invocation)
    - On bot reply (measured 3 days after posting)

**What is Measured: Affective Polarization**

* Sentiment analysis on replies to the bot (captured 3 days after posting)

**What is Measured: Antagonistic Affective Polarization**

* Antagonistic language classifier on replies to the bot (captured 3 days after posting)