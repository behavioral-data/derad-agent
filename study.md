## Research Question

How does the tone of an AI fact-check affect (1) engagement with the fact-check and (2) affective polarization in those exposed to the fact-check?

Hypothesis: Satirical AI fact-checks will decrease **antagonistic** affective polarization in those exposed to the fact-check but garner higher engagement than agreeable or neutral AI fact checks. Satirical fact-checks may or may not decrease **agonistic** affective polarization. (See related work section for definitions)

## Bot Details

We will have 3 bots, each of which has a different username. The bots will use the same underlying response generation architecture but each of them will use a different tone to reply.

* Agreeable Tone: aggie\_bot ☺️  
* Neutral Tone: nellie\_bot 🧐  
* Satirical (“Edgy”) Tone: eddie\_bot 😎

## Recruitment

We aim to recruit about 30 students from UW through a Google Form.

## Participant Task

We will ask participants to complete the following tasks:

* Before starting the study, participants will be asked to fill out a pre-survey.  
* Participants will be assigned 1 bot to interact with throughout the study.  
* Over a 5 day period, participants will be sent a search query that finds posts about a particular topic with high engagement (e.g., posts about immigration with at least 20 replies). They will be required to identify 10 posts that they disagree with, and mention their assigned bot in reply to those posts.  
* One day after the bot replies to a post, participants will be asked to fill out a DM survey about the quality of discussion evoked by the bot.  
* Participants will be asked to fill out a post-survey at the conclusion of the study.

## 

## Metrics \- Invokers

We will ask the following survey questions of **invokers**, who are hired to invoke our bot on posts that they disagree with.

### Pre-Survey (Google Forms)

**What is Measured: Antagonistic Affective Polarization**

* It is acceptable for \[members of your political party\] to send threatening and intimidating messages to leaders \[of an opposing political party\]. (0-10, strongly disagree to neutral to strongly agree)  
* It is justified for \[members of your political party\] to use violence in advancing their political goals these days. (0-10, strongly disagree to neutral to strongly agree)  
  * Adapted from Stray et al. (2026)’s survey questions for “support for political violence”  
* It is acceptable to manipulate political procedures to exclude \[members of an opposing political party\] from participating (0-10, strongly disagree to neutral to strongly agree)  
  * Informed by Westphal (2022)’s definition of antagonistic behavior as encompassing calls to manipulate political procedures to the exclusion of outgroups.  
* I am able to make change through political processes like voting (0-10, strongly disagree to neutral to strongly agree)  
  * Informed by Mouffe (2000)’s argument that an excess of consensus leads people who are dissatisfied with the status quo to feel that they have no options to make change through political processes (which leads to violent extremism)

**What is Measured: Agonistic Affective Polarization**

* Please indicate how you feel toward \[members of an opposing political party\] using the scale below. (0-10, very unfavorable/cold to neutral to very favorable/warm)  
* Please indicate how you feel toward \[members of your political party\] using the scale below. (0-10, very unfavorable/cold to neutral to very favorable/warm)  
* How comfortable are you having friends who are \[members of an opposing political party\]? (0-10, not at all to completely)  
* How comfortable are you having friends who are \[ingroup members\]? (0-10, not at all to completely)  
  * Adapted from Stray et al. (2026)’s survey questions for “affective polarization”

**What is Measured: Engagement**

* I feel able to engage in fact-checking posts that I disagree with on X (0-10, strongly disagree to neutral to strongly agree)

### Daily Surveys (X DMs)

**What is Measured: Engagement**

* Give the username of the author of the post you invoked the bot on  
  * Attention check to make sure participants read the post before answering the survey questions  
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
  * Measures quality of bot’s reply from participant’s perspective  
* In 2-3 sentences, explain your answer to the previous question  
* Overall, I would use this bot again (0-10, strongly disagree to neutral to strongly agree)  
  * Measures engagement from the participant side  
* In 2-3 sentences, explain your answer to the previous question

## 

## Metrics \- Bystanders

In addition to collecting the above metrics from invokers, we will also collect the following metrics about bot engagement from **bystanders**, or X users who are organically exposed to the bot replies. We assume that bystanders will largely consist of people who disagree with the fact-check.

**What is Measured: Engagement**

* Number of likes, reposts, and replies  
  * On original post (measured at time of invocation)  
  * On bot reply (measured 3 days after posting)

**What is Measured: Affective Polarization**

* Sentiment analysis on replies to the bot (captured 3 days after posting)

**What is Measured: Antagonistic Affective Polarization**

* Antagonistic language classifier on replies to the bot (captured 3 days after posting)