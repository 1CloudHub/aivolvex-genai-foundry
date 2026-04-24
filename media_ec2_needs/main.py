import json
import os
import subprocess
import uuid

import boto3
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Video subtitle data — populate these dicts with your actual segment data
# ---------------------------------------------------------------------------

CLIMATE_CHANGE = {
  "metadata": {
    "title": "Climate Change Explainer",
    "total_duration_seconds": 407.6,
    "format": "Each segment has start_time, end_time (in seconds), timestamp (HH:MM:SS), and text"
  },
  "segments": [
    {
      "id": 1,
      "start_time": 0.0,
      "end_time": 2.08,
      "timestamp": "00:00:00",
      "text": "Let's talk about climate change."
    },
    {
      "id": 2,
      "start_time": 2.08,
      "end_time": 5.48,
      "timestamp": "00:00:02",
      "text": "People are calling it the crisis of our time, and it is."
    },
    {
      "id": 3,
      "start_time": 10.96,
      "end_time": 14.52,
      "timestamp": "00:00:10",
      "text": "But it's easy to get lost in this story."
    },
    {
      "id": 4,
      "start_time": 14.52,
      "end_time": 18.08,
      "timestamp": "00:00:14",
      "text": "The science is dense, and politics get in the way."
    },
    {
      "id": 5,
      "start_time": 18.08,
      "end_time": 22.44,
      "timestamp": "00:00:18",
      "text": "World leaders are meeting in Madrid to talk about the climate crisis and how to slow it down."
    },
    {
      "id": 6,
      "start_time": 22.44,
      "end_time": 28.16,
      "timestamp": "00:00:22",
      "text": "And they're under pressure from millions of people around the world calling for concrete action."
    },
    {
      "id": 7,
      "start_time": 28.16,
      "end_time": 35.48,
      "timestamp": "00:00:28",
      "text": "The empty promises are the same and the inaction is the same. So what exactly are we doing wrong and how do we fix it?"
    },
    {
      "id": 8,
      "start_time": 41.44,
      "end_time": 47.4,
      "timestamp": "00:00:41",
      "text": "We're going to kick this off with some basic science. So bear with me, because this is important."
    },
    {
      "id": 9,
      "start_time": 47.4,
      "end_time": 53.16,
      "timestamp": "00:00:47",
      "text": "Look at this graph. These are the levels of carbon dioxide in our atmosphere over hundreds of thousands of years."
    },
    {
      "id": 10,
      "start_time": 53.16,
      "end_time": 58.68,
      "timestamp": "00:00:53",
      "text": "But this spike in carbon dioxide at the very end? That took off during the industrial revolution."
    },
    {
      "id": 11,
      "start_time": 58.68,
      "end_time": 64.24,
      "timestamp": "00:00:58",
      "text": "We started breaking CO2 records in 1950, and we haven't stopped since."
    },
    {
      "id": 12,
      "start_time": 65.36,
      "end_time": 70.92,
      "timestamp": "00:01:05",
      "text": "Well, scientists say there's a 95% chance that human activity is the cause."
    },
    {
      "id": 13,
      "start_time": 72.92,
      "end_time": 81.0,
      "timestamp": "00:01:12",
      "text": "We've been burning more and more fossil fuels like oil and coal, which release CO2, to power our homes, factories, airplanes and cars."
    },
    {
      "id": 14,
      "start_time": 81.0,
      "end_time": 87.88,
      "timestamp": "00:01:21",
      "text": "There's also a lot more of us. The global population has tripled in the past 70 years."
    },
    {
      "id": 15,
      "start_time": 87.88,
      "end_time": 93.72,
      "timestamp": "00:01:27",
      "text": "And we're consuming more products from animals that release another pollutant called Methane."
    },
    {
      "id": 16,
      "start_time": 93.72,
      "end_time": 104.4,
      "timestamp": "00:01:33",
      "text": "So all those gases are in the air, and when sunlight gets into the earth's atmosphere, some of the heat gets trapped, and the planet gets warmer. That's why they call it the Greenhouse Effect."
    },
    {
      "id": 17,
      "start_time": 107.8,
      "end_time": 116.88,
      "timestamp": "00:01:47",
      "text": "It's actually the warmest temperature on Earth since the last ice age, since 10 thousand years ago."
    },
    {
      "id": 18,
      "start_time": 116.88,
      "end_time": 124.16,
      "timestamp": "00:01:56",
      "text": "The UN says that right now, our world is about 1 degree hotter than pre-industrial times. That's around the year 1800."
    },
    {
      "id": 19,
      "start_time": 124.16,
      "end_time": 131.88,
      "timestamp": "00:02:04",
      "text": "Which is okay. In fact, the UN says if we warm by 1.5 degrees before the end of the century we should be fine."
    },
    {
      "id": 20,
      "start_time": 131.88,
      "end_time": 143.72,
      "timestamp": "00:02:11",
      "text": "The UN says even 2 degrees would probably be alright. But again, the problem is speed. Because right now, we are on track to hit 1.5 degrees in only ten years."
    },
    {
      "id": 21,
      "start_time": 143.72,
      "end_time": 153.48,
      "timestamp": "00:02:23",
      "text": "And if we don't slow that warming down, it could mean catastrophe within my lifetime, and maybe yours too. And we're already getting a taste."
    },
    {
      "id": 22,
      "start_time": 158.6,
      "end_time": 164.36,
      "timestamp": "00:02:38",
      "text": "Climate change is here. Climate change is happening. We are well into the 6th mass extinction event."
    },
    {
      "id": 23,
      "start_time": 166.44,
      "end_time": 169.12,
      "timestamp": "00:02:46",
      "text": "Europe is currently colder than the Arctic."
    },
    {
      "id": 24,
      "start_time": 169.12,
      "end_time": 173.12,
      "timestamp": "00:02:49",
      "text": "More than a thousand people being rescued just in the early morning hours of Sunday."
    },
    {
      "id": 25,
      "start_time": 173.12,
      "end_time": 177.28,
      "timestamp": "00:02:53",
      "text": "Millions of people are likely to suffer worsening food and water shortages."
    },
    {
      "id": 26,
      "start_time": 177.28,
      "end_time": 183.52,
      "timestamp": "00:02:57",
      "text": "The drought that's now in its tenth year is a phenomenon that's here to stay."
    },
    {
      "id": 27,
      "start_time": 183.52,
      "end_time": 190.12,
      "timestamp": "00:03:03",
      "text": "We've never seen a year's worth of rain in less than seven days."
    },
    {
      "id": 28,
      "start_time": 190.12,
      "end_time": 196.28,
      "timestamp": "00:03:10",
      "text": "Sea levels are rising about 3 millimetres a year because seawater expands as temperatures get warmer."
    },
    {
      "id": 29,
      "start_time": 196.28,
      "end_time": 202.32,
      "timestamp": "00:03:16",
      "text": "Melting ice sheets and glaciers also add trillions of tons of freshwater into our oceans."
    },
    {
      "id": 30,
      "start_time": 203.2,
      "end_time": 211.68,
      "timestamp": "00:03:23",
      "text": "People around the world are already losing their homes. And if things carry on, millions more of us will have to pack up too."
    },
    {
      "id": 31,
      "start_time": 211.68,
      "end_time": 220.16,
      "timestamp": "00:03:31",
      "text": "Entire coastal cities could be underwater within 80 years. Like Miami in the US or Osaka in Japan."
    },
    {
      "id": 32,
      "start_time": 220.16,
      "end_time": 224.44,
      "timestamp": "00:03:40",
      "text": "Entire island nations in the pacific could completely disappear."
    },
    {
      "id": 33,
      "start_time": 224.44,
      "end_time": 239.04,
      "timestamp": "00:03:44",
      "text": "Natural disasters becoming more and more intense, more frequent with devastating consequences. The dramatic impacts of droughts in different parts of the world, all of this is creating a situation that is a real threat to humankind. And we are not doing enough."
    },
    {
      "id": 34,
      "start_time": 240.04,
      "end_time": 251.16,
      "timestamp": "00:04:00",
      "text": "If 99% of doctors said to you, take this medicine, or you will get really sick and probably die, you would take it. Who wouldn't take it? The problem is, at the moment, we don't have any medicine."
    },
    {
      "id": 35,
      "start_time": 251.16,
      "end_time": 258.28,
      "timestamp": "00:04:11",
      "text": "Now, there is a plan to slow all this down. Back in 2016, world leaders signed the so-called Paris Agreement."
    },
    {
      "id": 36,
      "start_time": 258.28,
      "end_time": 266.72,
      "timestamp": "00:04:18",
      "text": "And the big pledge is to cap temperatures rising by 1.5 degrees or a maximum of 2, before the year 2100."
    },
    {
      "id": 37,
      "start_time": 266.72,
      "end_time": 278.0,
      "timestamp": "00:04:26",
      "text": "So countries set their own targets on how much CO2 they emit. But here's the thing — three years after the agreement, global CO2 levels are still going up."
    },
    {
      "id": 38,
      "start_time": 278.0,
      "end_time": 290.56,
      "timestamp": "00:04:38",
      "text": "CO2 emissions have been going up the last year by two per cent, so that's actually above the average of the last ten years. So it's started to increase again and it doesn't look too good. In some ways, we're going backwards."
    },
    {
      "id": 39,
      "start_time": 290.56,
      "end_time": 302.08,
      "timestamp": "00:04:50",
      "text": "The United States will cease all implementation of the nonbinding Paris accord and the draconian financial and economic burdens the agreement imposes on our country."
    },
    {
      "id": 40,
      "start_time": 302.08,
      "end_time": 312.36,
      "timestamp": "00:05:02",
      "text": "The US, one of the world's biggest polluters, has pulled out of the Paris deal. Russia and China are accused of not giving themselves ambitious targets in the first place."
    },
    {
      "id": 41,
      "start_time": 312.36,
      "end_time": 316.88,
      "timestamp": "00:05:12",
      "text": "Turkey and Poland want to build more power plants that use coal."
    },
    {
      "id": 42,
      "start_time": 316.88,
      "end_time": 329.12,
      "timestamp": "00:05:16",
      "text": "And then there's the sceptics. It's a political decision, that it's man-made global warming. We forced the computer models to say AHA! Human influence, CO2 and other stuff."
    },
    {
      "id": 43,
      "start_time": 329.12,
      "end_time": 334.48,
      "timestamp": "00:05:29",
      "text": "The ground base temperature data has been massaged to show an increase but the satellite data shows no increase."
    },
    {
      "id": 44,
      "start_time": 334.48,
      "end_time": 346.44,
      "timestamp": "00:05:34",
      "text": "On the other hand, there is positive momentum. There's more awareness and some countries are making progress. India, Morocco, and The Gambia have massive renewable energy projects."
    },
    {
      "id": 45,
      "start_time": 346.44,
      "end_time": 355.76,
      "timestamp": "00:05:46",
      "text": "There are different countries doing different things really successfully. Some countries are, for example, making all public transport free in the cities. What a great way to encourage people out of their cars."
    },
    {
      "id": 46,
      "start_time": 355.76,
      "end_time": 369.28,
      "timestamp": "00:05:55",
      "text": "But experts say what's needed now is an even bigger push to change everything about the way we run our world. Business as usual has got to change. Politics as usual has got to change. In order to combat that we have to change the system that has allowed it to happen. You can't have infinite growth on a finite planet."
    },
    {
      "id": 47,
      "start_time": 369.28,
      "end_time": 384.24,
      "timestamp": "00:06:09",
      "text": "And everyone can do that by shifting to renewable energy, reducing the use of cars, use trains more, cycle more, eat less meat, consume a bit more carefully."
    },
    {
      "id": 48,
      "start_time": 384.24,
      "end_time": 395.76,
      "timestamp": "00:06:24",
      "text": "So where does that leave us? Well there's only so much bike-riding and light-bulb replacing you and I can do everyday. But the truth is that it's those everyday things that are going to change anyway."
    },
    {
      "id": 49,
      "start_time": 395.76,
      "end_time": 407.6,
      "timestamp": "00:06:35",
      "text": "Even coffee could run out if farmers can't grow it. So the expert advice? Is that it's down to all of us, to change our ways and shake things up, or climate change is going to do it for us."
    }
  ]
}

A_SONG_FOR_MY_LAND = {
  "metadata": {
    "title": "Urgent Songs for My Land (Canciones Urgentes para Mi Tierra)",
    "total_duration_seconds": 2838.857,
    "format": "Each segment has start_time, end_time (in seconds), timestamp (HH:MM:SS), and text"
  },
  "segments": [
    {
      "id": 1,
      "start_time": 4.72,
      "end_time": 8.57,
      "timestamp": "00:00:04",
      "text": "Let's start with one. Okay, here it goes. Hey,"
    },
    {
      "id": 2,
      "start_time": 8.76,
      "end_time": 20.039,
      "timestamp": "00:00:08",
      "text": "I'm cold. That wasn't the case. No, I have an uncle named Mario."
    },
    {
      "id": 3,
      "start_time": 22.72,
      "end_time": 33.679,
      "timestamp": "00:00:22",
      "text": "I have an uncle named Mario. He's very funny and he's a veterinarian."
    },
    {
      "id": 4,
      "start_time": 33.679,
      "end_time": 44.52,
      "timestamp": "00:00:33",
      "text": "It cures the ailments of animals. Uncle Mario, veterinarian."
    },
    {
      "id": 5,
      "start_time": 46.079,
      "end_time": 56.8,
      "timestamp": "00:00:46",
      "text": "My prayer is a cry in the night. Mother, look at me in the night of my youth."
    },
    {
      "id": 6,
      "start_time": 62.6,
      "end_time": 74.6,
      "timestamp": "00:01:02",
      "text": "I don't know any of the songs on the entire list. This type of person needs to be available to discuss a couple of topics. Okay, let's take advantage of it, let's take advantage of it. Let's bring up new topics."
    },
    {
      "id": 7,
      "start_time": 73.36,
      "end_time": 79.64,
      "timestamp": "00:01:13",
      "text": "What do you want to do? What do you want? I don't know, something to remember. 68."
    },
    {
      "id": 8,
      "start_time": 79.52,
      "end_time": 86.52,
      "timestamp": "00:01:19",
      "text": "How lovely. That one was in the sun, I think. A topic. Let's see, it's in sol and re, I think it was. Sol."
    },
    {
      "id": 9,
      "start_time": 86.479,
      "end_time": 99.159,
      "timestamp": "00:01:26",
      "text": "Yes, sol and re for the master Gustavo. Tell me where they went on those bad nights, what trunk they kept, so many years with their guitars."
    },
    {
      "id": 10,
      "start_time": 99.159,
      "end_time": 107.439,
      "timestamp": "00:01:39",
      "text": "I don't know what became of me. I don't know what became of me in the silence of their voices."
    },
    {
      "id": 11,
      "start_time": 122.439,
      "end_time": 135.879,
      "timestamp": "00:02:02",
      "text": "It wasn't easy for you to get here because there is resistance. And what the community would like is for you to work on the marches, work on the anthems, sing those things."
    },
    {
      "id": 12,
      "start_time": 133.8,
      "end_time": 152.0,
      "timestamp": "00:02:13",
      "text": "The national anthem, the Sarmiento anthem, the anthem 'My Flag'. You are in a place that is full of people who have strong patriotic roots, if you want to use that term. And that's the ideal situation for you to enter and for the community to allow you to enter."
    },
    {
      "id": 13,
      "start_time": 171.4,
      "end_time": 176.239,
      "timestamp": "00:02:51",
      "text": "I was throwing it, I was calling him."
    },
    {
      "id": 14,
      "start_time": 178.159,
      "end_time": 188.519,
      "timestamp": "00:02:58",
      "text": "So, how are you all doing? Okay, okay. Well, I'm very glad. I am Professor Ramiro."
    },
    {
      "id": 15,
      "start_time": 185.92,
      "end_time": 198.159,
      "timestamp": "00:03:05",
      "text": "I've come to bring you music. I am a music teacher and I've come to sing songs, so we can learn together. I brought this instrument. Do you know what instrument it is?"
    },
    {
      "id": 16,
      "start_time": 195.36,
      "end_time": 207.879,
      "timestamp": "00:03:15",
      "text": "The guitar. The guitar. Uh, the classical guitar. I like playing other types of guitars too. Yes, but well, today I brought this so I could sing a little bit of this song. Do you want to listen to it?"
    },
    {
      "id": 17,
      "start_time": 207.879,
      "end_time": 221.0,
      "timestamp": "00:03:27",
      "text": "Yes, yes. It was the struggle, your life and your element, fatigue, your rest and calm."
    },
    {
      "id": 18,
      "start_time": 237.0,
      "end_time": 247.28,
      "timestamp": "00:03:57",
      "text": "Look, with audio and everything. Hi Silvia, how are you? Hey, Silvia, I'm going to apply herbicide today."
    },
    {
      "id": 19,
      "start_time": 245.36,
      "end_time": 261.32,
      "timestamp": "00:04:05",
      "text": "Uh, the combo is intense, so I have to find a time when the kids aren't at school. Maybe today they can finish a little earlier and we can do a few passes because we need a north wind so we don't burn our little trees."
    },
    {
      "id": 20,
      "start_time": 259.519,
      "end_time": 275.52,
      "timestamp": "00:04:19",
      "text": "Yes. And where? I forgot the recipe and the school is right next door. Yes, it's really stuck on, huh? So I suggest that, if they're coming around 6, let's work until 5 and then leave."
    },
    {
      "id": 21,
      "start_time": 284.72,
      "end_time": 301.72,
      "timestamp": "00:04:44",
      "text": "And what did you do? What happened? We went inside. We never had any. We didn't have a little game. They didn't have a game. We didn't have a game. If it weren't for the smell."
    },
    {
      "id": 22,
      "start_time": 298.36,
      "end_time": 301.72,
      "timestamp": "00:04:58",
      "text": "Yes, we bored him."
    },
    {
      "id": 23,
      "start_time": 302.08,
      "end_time": 311.32,
      "timestamp": "00:05:02",
      "text": "Poison. Poison. Poison does no harm to the body. Yes. The sign started to get in her throat because that's bad for her. The head."
    },
    {
      "id": 24,
      "start_time": 389.16,
      "end_time": 396.639,
      "timestamp": "00:06:29",
      "text": "Who would like to play the guitar? I. Me?"
    },
    {
      "id": 25,
      "start_time": 391.56,
      "end_time": 404.919,
      "timestamp": "00:06:31",
      "text": "So. Well. And who would like to sing? I did keep thinking about what happened in the other class. I have an idea and I wanted to tell you about it. Would you like to sing songs about taking care of our Earth?"
    },
    {
      "id": 26,
      "start_time": 404.319,
      "end_time": 422.919,
      "timestamp": "00:06:44",
      "text": "Yes. Well, but for that you have to write the songs. To write them we have to start thinking in poetry, right? All those environmental problems, right? We should also start thinking about how we can express them poetically."
    },
    {
      "id": 27,
      "start_time": 429.639,
      "end_time": 443.199,
      "timestamp": "00:07:09",
      "text": "Our school, the rural school. How can I put it another way? Think about it. Using poetry. What do you think?"
    },
    {
      "id": 28,
      "start_time": 442.319,
      "end_time": 457.319,
      "timestamp": "00:07:22",
      "text": "An island in the middle of the sea. It looks like what? An island. I like that. Uh, it could be an island in the middle of a green ocean. Green."
    },
    {
      "id": 29,
      "start_time": 457.319,
      "end_time": 466.026,
      "timestamp": "00:07:37",
      "text": "Look, did you see? If I say crop duster. What do you think? Mosquito, mosquito, mosquito, poisonous mosquito, or toxic mosquito."
    },
    {
      "id": 30,
      "start_time": 481.199,
      "end_time": 494.759,
      "timestamp": "00:08:01",
      "text": "What other word can you make instead of poisonous mosquito? What do you think? A metal dove flew by."
    },
    {
      "id": 31,
      "start_time": 491.599,
      "end_time": 502.08,
      "timestamp": "00:08:11",
      "text": "Metal doves. Metal doves. I like that, huh? Pigeon. Metal dove. And I'm saying the dove. The dove is. Did you see the dove of peace?"
    },
    {
      "id": 32,
      "start_time": 502.08,
      "end_time": 510.639,
      "timestamp": "00:08:22",
      "text": "Are there any birds that aren't very friendly? Yes, yes, yes."
    },
    {
      "id": 33,
      "start_time": 511.8,
      "end_time": 526.026,
      "timestamp": "00:08:31",
      "text": "The carancho passes by. The carancho. There it is. It could be a garancho. What would it look like? Metal garancho. Metal hook, metal."
    },
    {
      "id": 34,
      "start_time": 532.399,
      "end_time": 550.76,
      "timestamp": "00:08:52",
      "text": "What I like most are lettuces. The topic of fumigation is not discussed in this area. I know it's complicated, but well, I've tried in the cooperatives."
    },
    {
      "id": 35,
      "start_time": 542.279,
      "end_time": 570.48,
      "timestamp": "00:09:02",
      "text": "I ask you, who are the participants in the parent-teacher associations that are operating in schools? People from the countryside who own land. But I ask you, if not us, who is going to say that these topics can't be discussed? If we don't talk about these issues, who will?"
    },
    {
      "id": 36,
      "start_time": 570.48,
      "end_time": 583.6,
      "timestamp": "00:09:30",
      "text": "What are you going to do if one day they tell you, well, let's see, submit your resignation because you are not fulfilling your duties in the music workshop, because that can happen too."
    },
    {
      "id": 37,
      "start_time": 607.846,
      "end_time": 641.799,
      "timestamp": "00:10:07",
      "text": "Yes. Let's see. Come on. Attention, expensive metal that the sky, metal vulture, don't throw any more fingers. What do you think? It might work."
    },
    {
      "id": 38,
      "start_time": 648.639,
      "end_time": 674.399,
      "timestamp": "00:10:48",
      "text": "The same thing you want to work, I'm living it here, Ramiro. Nobody talks about the consequences of cancer. And you're going to tell me, yes, but it's not just that, there are other medical issues. And we know we're in the middle."
    },
    {
      "id": 39,
      "start_time": 665.639,
      "end_time": 694.399,
      "timestamp": "00:11:05",
      "text": "You're in the middle of being fumigated, of being passed around, and nobody is going to tell you the truth. Sometimes you go and ask, 'Hey, don't worry, I'm going through a rough patch.' But it's not harmless. And you go outside and you can't breathe."
    },
    {
      "id": 40,
      "start_time": 683.519,
      "end_time": 714.399,
      "timestamp": "00:11:23",
      "text": "And you think anyone here is going to respect us? Do you think I, from this rural school, being so small as we are, can go out and say, 'Look, you know what? Let's respect things. Let's take care of this space. Certain activities can't be done at certain distances. At least let me know. At least let me know, hey, look, tomorrow I'm going to do this.' That's what it means to be invisible."
    },
    {
      "id": 41,
      "start_time": 718.56,
      "end_time": 745.68,
      "timestamp": "00:11:58",
      "text": "Would you like us to record the song to play it on the radio later? Yes. Good. This here is part of a recording studio that will capture our voices. What we sing will be recorded so that other people who don't know us can know what's happening to us here in the rural school and why we sing what we sing."
    },
    {
      "id": 42,
      "start_time": 747.839,
      "end_time": 766.399,
      "timestamp": "00:12:27",
      "text": "Listen, it's raining here, it's raining in the headphones. I want to listen to see if it works. Okay, are you ready? Here we go. Listen."
    },
    {
      "id": 43,
      "start_time": 769.04,
      "end_time": 780.399,
      "timestamp": "00:12:49",
      "text": "We are an island in the middle of this green sea. Fresh air."
    },
    {
      "id": 44,
      "start_time": 780.44,
      "end_time": 789.639,
      "timestamp": "00:13:00",
      "text": "It's great that it turned out so well. Very good, huh. Let's show the song to everyone who wants to hear it."
    },
    {
      "id": 45,
      "start_time": 794.399,
      "end_time": 804.48,
      "timestamp": "00:13:14",
      "text": "Okay, let's do it like this. Come to the radio station on Wednesday, bring the song, and we'll do a story for it to air next week."
    },
    {
      "id": 46,
      "start_time": 808.399,
      "end_time": 835.16,
      "timestamp": "00:13:28",
      "text": "How are you, Ramiro? Excuse the late hour. Today we're not in the same mood as other times you've come because we had an incident that we'll tell you about with the kids. Friday, 3:30 in the afternoon, what were you doing? We were at the gym, and then I realized: we went outside, then I had my milk, we went out to recess, we played, and then suddenly the mosquito appeared."
    },
    {
      "id": 47,
      "start_time": 834.48,
      "end_time": 861.56,
      "timestamp": "00:13:54",
      "text": "It was hanging onto the fence, it wouldn't stop. Then the teacher said, 'Teacher, turn over there,' and then the owner of the field came and it left. But the mosquito was being fumigated. Yes, until it stopped."
    },
    {
      "id": 48,
      "start_time": 841.199,
      "end_time": 883.839,
      "timestamp": "00:14:01",
      "text": "At first, when we had come here, we saw it, we turned over, and we didn't pay attention to it, and then that afternoon when we were playing, it was spraying like this over there, and then it seeped in, and that liquid was coming this way, towards the school."
    },
    {
      "id": 49,
      "start_time": 884.6,
      "end_time": 897.56,
      "timestamp": "00:14:44",
      "text": "Did you notice? Did you feel it? What did you feel? My nose was burning, his face was burning. Your nose was burning, your face."
    },
    {
      "id": 50,
      "start_time": 894.0,
      "end_time": 903.12,
      "timestamp": "00:14:54",
      "text": "Actually, what hurt the most was the headache. It barely made your head hurt."
    },
    {
      "id": 51,
      "start_time": 904.56,
      "end_time": 918.959,
      "timestamp": "00:15:04",
      "text": "And what? It happens a lot, but not to this extent. On top of that, sir, they were all dressed up inside the fumigation truck with the suits, because whoever is protected has the advantage."
    },
    {
      "id": 52,
      "start_time": 907.32,
      "end_time": 935.44,
      "timestamp": "00:15:07",
      "text": "He says, well, whoever is inside, wearing the mask and the suit, at least they're protected inside. Who were the only people who weren't protected on Friday? We were."
    },
    {
      "id": 53,
      "start_time": 935.44,
      "end_time": 942.04,
      "timestamp": "00:15:35",
      "text": "Then when we were leaving, there was another one, a red one."
    },
    {
      "id": 54,
      "start_time": 969.72,
      "end_time": 1000.839,
      "timestamp": "00:16:09",
      "text": "Yes. Regarding that note in the song... Carancho, in any case, Romy, the next interviews you have with some people who are extremists, we'll see them beforehand, because there's a total dismissal of issues like agrochemicals that have been used for decades with scientific studies that support them and with laws that allow it."
    },
    {
      "id": 55,
      "start_time": 992.16,
      "end_time": 1014.48,
      "timestamp": "00:16:32",
      "text": "I don't want to upset you or anything, but the song isn't going to be released and neither is the interview. I understand the need to talk about the environment and that's perfectly fine and it has to be addressed and all, but with more caution. So, well, don't take this the wrong way. I always tell you this with the utmost sincerity."
    },
    {
      "id": 56,
      "start_time": 1012.72,
      "end_time": 1030.919,
      "timestamp": "00:16:52",
      "text": "Who sent you that, man? Well, here's the story: a journalist was going to put together an interview with Carancho and some texts I gave her. Uh, the idea was that all this would come out next week on the radio, but then the director sends an audio message dismissing the interview."
    },
    {
      "id": 57,
      "start_time": 1029.319,
      "end_time": 1052.16,
      "timestamp": "00:17:09",
      "text": "Listen, a guy is telling you that it's scientifically proven that agrochemicals do nothing, but where did that come from? I don't know if he believes it, or if it suits him. Does it suit him, or does he turn a deaf ear, or does he not even think about it? Well, whatever brings him money, whatever his interest is."
    },
    {
      "id": 58,
      "start_time": 1057.72,
      "end_time": 1083.28,
      "timestamp": "00:17:37",
      "text": "Yes, yes. I think they wanted the story to be about kids from rural schools singing to nature, you know? Little songs of light to see if the fireflies appear. All very colorful, but it avoids the most important thing because otherwise we get stuck on, as the little song says, on the kids singing. How nice."
    },
    {
      "id": 59,
      "start_time": 1077.72,
      "end_time": 1090.2,
      "timestamp": "00:17:57",
      "text": "It's nice, but behind them the kids are saying things, and they come from them. The issue is there's censorship. Yes, but we're also being lukewarm. Yes, I agree too. Otherwise, it can't be done with this topic."
    },
    {
      "id": 60,
      "start_time": 1097.6,
      "end_time": 1124.559,
      "timestamp": "00:18:17",
      "text": "Woodstock. An incredible film, an incredible event is back."
    },
    {
      "id": 61,
      "start_time": 1125.36,
      "end_time": 1166.799,
      "timestamp": "00:18:45",
      "text": "Well, that video we saw is from a great concert called Woodstock. Yes, it's a concert that took place more than fifty years ago in the United States, in a place like the countryside, right? Like where we are now. Did you see all the people there? A lot of young people, singing songs to fight for peace and also to defend our planet, because many of those young people were the first environmentalists."
    },
    {
      "id": 62,
      "start_time": 1163.36,
      "end_time": 1200.12,
      "timestamp": "00:19:23",
      "text": "What do you think if instead of playing the song on the radio, we sing it at a big concert? A big concert with a lot of artists right here, nearby, here in the countryside. Yes. Do you think it's a good idea? Yes."
    },
    {
      "id": 63,
      "start_time": 1181.0,
      "end_time": 1218.799,
      "timestamp": "00:19:41",
      "text": "A pleasure for the environment. Yes. What do I know? At that moment, it was what came to me in San Marcos. And well, they don't want to play it on the radio, so we'll have to do it live on a big scale. I can't do it alone, that's why I'm telling you. Imagine what a huge stage in the middle of the countryside would be like with these boys and girls singing to take care of our land with all the artists singing in that place."
    },
    {
      "id": 64,
      "start_time": 1222.76,
      "end_time": 1258.799,
      "timestamp": "00:20:22",
      "text": "Who would you like to participate in this song? Luciano Pereira. Luciano Pereira. You. How great to invite you. It would be fantastic to invite you. A friend. León. León, León. Well, that would be great. We'll do everything possible. Who else? Abel Pintos. I always wanted to see María Becerra. Would you like to invite María Becerra? Well, let's work on that then and see which artists respond."
    },
    {
      "id": 65,
      "start_time": 1261.559,
      "end_time": 1275.12,
      "timestamp": "00:21:01",
      "text": "León Divididos. Very good. The red. My brush is wearing out. If my finger wears out, I have nine more fingers."
    },
    {
      "id": 66,
      "start_time": 1279.159,
      "end_time": 1298.76,
      "timestamp": "00:21:19",
      "text": "The light purple cherry. Come on, we're almost there. Let's paint it a little more. Okay, help us out, sing with us."
    },
    {
      "id": 67,
      "start_time": 1301.799,
      "end_time": 1318.76,
      "timestamp": "00:21:41",
      "text": "Dear Cubero, how are you? I'm leaving you this audio to ask if you have any contacts with Fitopez."
    },
    {
      "id": 68,
      "start_time": 1324.559,
      "end_time": 1365.76,
      "timestamp": "00:22:04",
      "text": "If you're following this and thinking that it's going to happen, and that it's going to be real, it will be real. Well, we have to work on it. I need to have a few more classes to have more contact with them so I can continue developing themes and songs and rehearsals and add other schools too, because they could help me."
    },
    {
      "id": 69,
      "start_time": 1345.52,
      "end_time": 1394.84,
      "timestamp": "00:22:25",
      "text": "Well, you saw that the project started to spread, we're mostly rural schools. Well, and how do the other schools see it? For now, I can tell you that Vale from the Cayuqueo school is super involved and I know she would love to be part of it and have her students be part of the project."
    },
    {
      "id": 70,
      "start_time": 1376.039,
      "end_time": 1414.72,
      "timestamp": "00:22:56",
      "text": "So, as a last resort, I say, well, when you can't come to my school, you can go to Vale's school. It's all on the main road. Okay. Go ahead. Welcome, birds. About the birds. And what was the problem that was causing their extinction? They were killing a lot of straw and things like that."
    },
    {
      "id": 71,
      "start_time": 1386.48,
      "end_time": 1445.2,
      "timestamp": "00:23:06",
      "text": "Well, look, you saw that while chatting a bit we started talking about environmental problems, and you have a very interesting one. Would you like us to write something about it? Yes. And today my students came here to sing you a song so we can learn to sing it together. Does that sound good? Okay, let's go."
    },
    {
      "id": 72,
      "start_time": 1391.24,
      "end_time": 1449.881,
      "timestamp": "00:23:11",
      "text": "Urgent Songs for My Land. It's an environmental education project that started with kids like you who began to worry about what was happening around the schools, and we decided to write a first song, and today I'm here to propose presenting those songs here, and we're going to perform them in the classroom with you. Would you like to be part of this band? Yes."
    },
    {
      "id": 73,
      "start_time": 1440.2,
      "end_time": 1498.159,
      "timestamp": "00:24:00",
      "text": "Here's another problem, do you think we can address and write a song about? Another song about how they're cutting down so many trees in the forests. And what happens if the world floods and disappears? The water runs out, the grass runs out, the trees, and we won't have any oxygen. When there are fires, what is lost? The plants, and the oxygen is contaminated. The Amazon, agony. The Amazon, land, wounded."
    },
    {
      "id": 74,
      "start_time": 1476.48,
      "end_time": 1518.159,
      "timestamp": "00:24:36",
      "text": "Let's try the Aboriginal heartbeat. One, two, one, two, three. Let's do our once. It can be louder. There's a lot of echo. With energy, but without shouting."
    },
    {
      "id": 75,
      "start_time": 1504.159,
      "end_time": 1538.159,
      "timestamp": "00:25:04",
      "text": "We are an island in the middle of this. I loved it. Let's do one more take. We want to go out into the countryside and breathe."
    },
    {
      "id": 76,
      "start_time": 1579.48,
      "end_time": 1614.559,
      "timestamp": "00:26:19",
      "text": "I already have some surprises. Yes, there's an artist who sent me an audio that I want you to hear. Let's see, let's see, here it is. Uh, hi Ramiro, thanks for the songs. Hey, you know what's amazing, right?"
    },
    {
      "id": 77,
      "start_time": 1598.44,
      "end_time": 1642.6,
      "timestamp": "00:26:38",
      "text": "This project of yours that's been hidden away, it has a lot of value. Well, this weekend I'm going to spend a little time looking at this song, you know? It's the least I can do for this project, and I'm going to get a little more involved in this project that I think is unique, truly unique."
    },
    {
      "id": 78,
      "start_time": 1622.6,
      "end_time": 1683.559,
      "timestamp": "00:27:02",
      "text": "This gentleman is Mr. Leoniec, who says he's joining the project and that he's going to sing metal with us, and we're going to invite other artists as well. We have León, we have the rural schools, we have the songs ready, it's like everything's set, but I need the municipal permit."
    },
    {
      "id": 79,
      "start_time": 1639.84,
      "end_time": 1702.24,
      "timestamp": "00:27:19",
      "text": "Yes, I think now is the time to announce it, that there's a possible concert where these artists will come, and sing these songs with our boys and girls. But for that, because I've already written to some artists, they're already asking me, but when would this happen? Of course, we have to set a date, we have to book a date and the permit and venue."
    },
    {
      "id": 80,
      "start_time": 1664.36,
      "end_time": 1738.88,
      "timestamp": "00:27:44",
      "text": "Everything just kind of fell into place, schools joined, they started adding artists, artists started saying yes, and we already have songs composed with students from here and other schools. And I thought, how great it would be to have a kind of outdoor, ambient concert. I was thinking about that concert at the end of the 60s, the Woodstock, where our kids are the protagonists, where their voices are the protagonists, since it's unique."
    },
    {
      "id": 81,
      "start_time": 1679.559,
      "end_time": 1768.64,
      "timestamp": "00:27:59",
      "text": "I don't know if there's another precedent for something so big here. I don't think so. I might have some idea of this, and sometimes I don't. It makes me... not afraid, but I say, well, yes, to start planning it, to start thinking about the structural aspects, and that's where I'm going to start asking for help, from all the people who have organized something like this before, to do something that's never been done in the area."
    },
    {
      "id": 82,
      "start_time": 1728.88,
      "end_time": 1800.0,
      "timestamp": "00:28:48",
      "text": "They're going to help us organize this event. Do you have the permit? I have the permit to hold an event, a bust, let's say, here in San Marcos. That's already done. But not only the permit, I have the permit and a few other things. Here, you have the place, you have the grounds."
    },
    {
      "id": 83,
      "start_time": 1785.559,
      "end_time": 1828.0,
      "timestamp": "00:29:45",
      "text": "There's the one belonging to the Gaucha group, which is a grounds where they do horse breaking and other things, and it's almost on the border between the town and the countryside. I have something. For me, with the artists, you already have León Jeco confirmed, that's it."
    },
    {
      "id": 84,
      "start_time": 1819.88,
      "end_time": 1833.919,
      "timestamp": "00:30:19",
      "text": "We can say yes, we can say yes. If you have León Jeco confirmed, for me it's done, it's happening. I mean, it's to start putting it together. Thank you."
    },
    {
      "id": 85,
      "start_time": 1697.48,
      "end_time": 1862.88,
      "timestamp": "00:28:17",
      "text": "One, two, three, four. One, two, three, four. Today we're going to have the first big rehearsal. Other schools and choirs will also be joining, right? Other students from other rural schools. Luna, a lonely sorrow, sun."
    },
    {
      "id": 86,
      "start_time": 1835.0,
      "end_time": 1870.32,
      "timestamp": "00:30:35",
      "text": "We're going to have a huge concert, a kind of environmental celebration here in the countryside. We're going to invite many artists from different genres to sing: folk, tango, rock. It's a project that has no precedent in Argentina or in the Americas, but it was born in rural areas and continues to thrive there."
    },
    {
      "id": 87,
      "start_time": 1870.32,
      "end_time": 1889.64,
      "timestamp": "00:31:10",
      "text": "And the logistics of all that. What you're proposing is crazy. It was important to come and see the stage setup. It's going to be complicated when they tell us everything that's needed for the organization."
    },
    {
      "id": 88,
      "start_time": 1885.559,
      "end_time": 1901.72,
      "timestamp": "00:31:25",
      "text": "We have to keep in mind that we don't have a penny. Education, culture, environment. It can't be that they don't contribute anything. Give me a van, lodging, the land, you can't uproot the flower."
    },
    {
      "id": 89,
      "start_time": 1901.72,
      "end_time": 1936.559,
      "timestamp": "00:31:41",
      "text": "I wanted to get in touch with a representative of the environmental group. Can 1,000 people come or can 10,000 people come? We don't know. I don't know if we're ready, but we have to make an effort."
    },
    {
      "id": 90,
      "start_time": 1914.679,
      "end_time": 1952.755,
      "timestamp": "00:31:54",
      "text": "The idea is that everyone is up there wearing white lab coats. If you don't have one, we're getting lab coats for you. Seriously though, about the 12th, they're going to dress up as little bees."
    },
    {
      "id": 91,
      "start_time": 1953.0,
      "end_time": 1978.355,
      "timestamp": "00:32:33",
      "text": "Applause for everyone. Everyone, everyone, everyone, everyone. Yes, it doesn't matter if it falls. We're going to use that photo to invite everyone to come to the concert."
    },
    {
      "id": 92,
      "start_time": 1971.88,
      "end_time": 2010.36,
      "timestamp": "00:32:51",
      "text": "We could be on a dirt road, and here where this line is, the fence, and over there, the whole field. A plane flew over there. Another thing, well, what would be a good protective measure, like gloves or masks? I made up that a plane was here spraying."
    },
    {
      "id": 93,
      "start_time": 2001.399,
      "end_time": 2054.6,
      "timestamp": "00:33:21",
      "text": "Yes, but how do we put it in the picture? Really, but it's a good idea because the kids got on a plane there, so everyone should have a tank of air, the mask and the mask. Okay, each of you is going to put on one of these masks. Yes."
    },
    {
      "id": 94,
      "start_time": 1945.399,
      "end_time": 2073.6,
      "timestamp": "00:32:25",
      "text": "We took that photo for one of the songs, and it specifically portrays one of the foundation's problems regarding schools."
    },
    {
      "id": 95,
      "start_time": 2057.279,
      "end_time": 2099.2,
      "timestamp": "00:34:17",
      "text": "Yes, what I wanted to say was that that photo is going to create some obstacles, some rejections, some problems, so ideally the message should be more general and not touch on those sensitive and direct issues. Because of this, it would be best to lower your profile because if we continue with this topic, it might be detrimental to the project, and the idea is to tone things down a bit to be able to move forward."
    },
    {
      "id": 96,
      "start_time": 1990.799,
      "end_time": 2134.44,
      "timestamp": "00:33:10",
      "text": "Okay, about the event. I'd like you to explain to the kids how it's used so they don't get confused, so the kids don't misinterpret it, because there are people who work like you, who are dedicated to this, and we live off the land. Otherwise, if not the land, we wouldn't live. I'm an applicator, and doing things properly, it's not poison. These things need to be explained because, as we've seen, there are many people who don't know and take it the wrong way. To talk about something, one has to be certain."
    },
    {
      "id": 97,
      "start_time": 2037.279,
      "end_time": 2145.879,
      "timestamp": "00:33:57",
      "text": "Sometimes I risk going to extremes because if I don't go to extremes with something, nobody can mess with me. We wouldn't have had this conversation if you hadn't heard that. No, I didn't hear it, and here you are. I see you like this, I come down, from a rural school, fighting for it for nothing, crazy, and I come to find you here."
    },
    {
      "id": 98,
      "start_time": 2131.839,
      "end_time": 2165.879,
      "timestamp": "00:35:31",
      "text": "But well, but listen, I'm going to my students, I mean, but I'm not here to tell the truth, no. Your dad might have the truth, I have it. Maybe together we can build a truth to be better off."
    },
    {
      "id": 99,
      "start_time": 2081.679,
      "end_time": 2218.24,
      "timestamp": "00:34:41",
      "text": "The news is coming up, the date is approaching, and the breeding ground is getting more and more... On the site, I'm telling you, it's very difficult for it to happen, and he didn't give me a choice, and he repeats again, to see if it can be postponed."
    },
    {
      "id": 100,
      "start_time": 2103.52,
      "end_time": 2230.0,
      "timestamp": "00:35:03",
      "text": "What they told me is, look, there's pressure, and we mayors are the scapegoats, a bit. Yes. And there are mayors who agree, and those who don't agree, it's not that they don't agree with us, but they understand, 'Hey, well, but we have to continue governing.' So we don't have a place to hold the concert, let's say."
    },
    {
      "id": 101,
      "start_time": 2146.96,
      "end_time": 2272.52,
      "timestamp": "00:35:46",
      "text": "The event has been suspended today. Yes, yes, because you can't come today after so much and say today it's not happening, Ramiro, because it's unfeasible for you to say that because we moved a lot and we didn't play much either, because now, how do you go back to everything? Yes, I, the people, the folks, the kids, Ramiro, what was generated. Yes, this is saying no to everything we said to each other."
    },
    {
      "id": 102,
      "start_time": 2194.0,
      "end_time": 2317.96,
      "timestamp": "00:36:34",
      "text": "Yes, no producer came to me and said, 'Ramiro, I don't agree with what you're doing.' It's with big corporations, and that's how it is. And we also have to show that we have power, we have the power to make this a scandal, for someone to say, 'No, no, it's better to do it than not to do it.' We have to do everything we can. For me, we have to keep pushing it, so they see that we're publishing things that are being done."
    },
    {
      "id": 103,
      "start_time": 2267.96,
      "end_time": 2338.28,
      "timestamp": "00:37:47",
      "text": "Ramiro, listen to me. Don't pay any attention to those three guys, they don't really understand a thing, you know? You're not against the countryside, you're actually in favor of it, you see? But I think we have to keep pushing, keep looking for artists who will support this registry we're going to create, and say, 'Hey, don't mess with art.'"
    },
    {
      "id": 104,
      "start_time": 2281.96,
      "end_time": 2395.359,
      "timestamp": "00:38:01",
      "text": "Ramiro, how are you doing? It would be great to do a really good interview to get the approval of all the artists. Even if everything goes dark, even if no one responds, even if the rain doesn't stop, even if the moon hides, like Baltazar, like Fatoruso, Malosetti, those bands like La Renga, it's been a pleasure."
    },
    {
      "id": 105,
      "start_time": 2369.2,
      "end_time": 2415.359,
      "timestamp": "00:39:29",
      "text": "How great, man. On November 12th, we're playing in my town to sing this song, León León, and send a huge hug to all the guys. To all the guys. It's truly an honor, but for us, this project is very beautiful."
    },
    {
      "id": 106,
      "start_time": 2403.359,
      "end_time": 2456.839,
      "timestamp": "00:40:03",
      "text": "Things aren't working out in a way that allows us to do it. There are some obstacles. We have to make it happen. Yes, I think that if so much has been achieved so far, I feel flattered to participate in a project like this."
    },
    {
      "id": 107,
      "start_time": 2414.319,
      "end_time": 2500.16,
      "timestamp": "00:40:14",
      "text": "Always available for this kind of collaborations. I think it makes more sense than ever that the networks that are built among artists when we have a little visibility can serve for a cause that is more than noble. We're talking about a first-rate lineup, a selection of very big names in music. Yes, every time I talk about it, it still seems like a dream."
    },
    {
      "id": 108,
      "start_time": 2378.28,
      "end_time": 2516.839,
      "timestamp": "00:39:38",
      "text": "We want to do a concert on November 12th in San Marcos Sur, in the heart of the countryside, with important bands, but also with bands that don't have the same level of recognition because they're from here in the countryside. A project without precedent in Argentina or in the Americas, and for that, we need everyone's support to make this dream a reality."
    },
    {
      "id": 109,
      "start_time": 2516.839,
      "end_time": 2559.52,
      "timestamp": "00:41:56",
      "text": "This is Rubén Blaz greeting Professor Lescano. People for my land, the kids from Córdoba. We're together, with all my heart I sing urgent songs for my land."
    },
    {
      "id": 110,
      "start_time": 2519.2,
      "end_time": 2576.079,
      "timestamp": "00:41:59",
      "text": "Hello friends, I'm Gocio, Javier Calamaro, here's Lula Bertold and Abel Pintos, and I want to congratulate everyone who is part of this project. It seems like a dream. I can't believe it. Thank you. Well, thank you. Thank you very much. The project is beautiful. Thank you very much. My pleasure."
    },
    {
      "id": 111,
      "start_time": 2589.2,
      "end_time": 2641.88,
      "timestamp": "00:43:09",
      "text": "The support you got is tremendous. It's tremendous what was put together. Do one thing, come now. Come now because it's time to get another place."
    },
    {
      "id": 112,
      "start_time": 2646.96,
      "end_time": 2701.56,
      "timestamp": "00:44:06",
      "text": "Silvia is at school. Listen to me. The concert is happening. Yes, yes. They gave us another venue. Yes. No, I can't believe it. Ah, I don't have one, but I'll join yours."
    },
    {
      "id": 113,
      "start_time": 2688.76,
      "end_time": 2701.56,
      "timestamp": "00:44:48",
      "text": "Uh, after so long. Cheers. Here in town. Yes, it's another venue, but here in town it's like breathing. Fresh air."
    },
    {
      "id": 114,
      "start_time": 2731.44,
      "end_time": 2762.44,
      "timestamp": "00:45:31",
      "text": "Please, with up there. Attention, now yes, okay, let's have one last little bit of silence because we're leaving."
    },
    {
      "id": 115,
      "start_time": 2762.839,
      "end_time": 2816.72,
      "timestamp": "00:46:02",
      "text": "We worked a long time for this. We worked really hard, yes. Many rehearsals, sacrifices, fun, smiles. Today there are a lot of people there at the venue waiting for us to bring our urgent songs. So, a lot of responsibility, a lot of joy, let's enjoy it, have a good time, right?"
    },
    {
      "id": 116,
      "start_time": 2803.16,
      "end_time": 2836.72,
      "timestamp": "00:46:43",
      "text": "Beautiful, super beautiful. There's a huge stage for you and all the people there who are waiting for you. So let's sing. Here we go then. Let's go. Very good."
    },
    {
      "id": 117,
      "start_time": 2529.639,
      "end_time": 2592.76,
      "timestamp": "00:42:09",
      "text": "León Jeco confirmed. We can say yes. If you have León Jeco confirmed, for me it's done, it's happening. I mean, it's to start putting it together. Thank you. For the pleasure, for the mental pleasure, for Ramiro."
    },
    {
      "id": 118,
      "start_time": 2569.814,
      "end_time": 2700.839,
      "timestamp": "00:42:49",
      "text": "One, two, three, four. One, two, three, four. Today we're going to have the first big rehearsal. Other schools and choirs will also be joining, right? Other students from other rural schools. Luna, a lonely sorrow, sun."
    },
    {
      "id": 119,
      "start_time": 2763.814,
      "end_time": 2838.857,
      "timestamp": "00:46:03",
      "text": "All these people came to see you. Welcome to the great concert of urgent songs. Welcome to this great first ambient taste."
    },
    {
      "id": 120,
      "start_time": 2684.48,
      "end_time": 2773.04,
      "timestamp": "00:44:44",
      "text": "Sanos, the region, the country is ready for what's coming. The central moment of the urgent concert for my land. Get ready, we're going up. Today is the today on this stage. A big round of applause for Lito Vitales. A big round of applause for León Diego. With us, Mr. Ramiro Lescano, Professor, and his students from the rural schools. A big round of applause."
    },
    {
      "id": 121,
      "start_time": 2694.96,
      "end_time": 2817.68,
      "timestamp": "00:44:54",
      "text": "Well, 'Urgent Songs for My Land' is a project born deep in the countryside, embodied in songs composed by my students that address a wide range of environmental issues. Just now, a media outlet asked me if this concert is against the countryside. How can it be against the countryside if we are the countryside? Those over there are the rural schools. Yes, we are producers too, but we produce songs, art from a beautiful place, which is the countryside. And well, here are the children who want to be heard, who want to sing their song. Thank you very much."
    },
    {
      "id": 122,
      "start_time": 2689.119,
      "end_time": 2838.857,
      "timestamp": "00:44:49",
      "text": "Lord, Lord, look at the plane. May you be the sky of metal. Metal vulture, soldier of death. Metal vulture. It's not sickening to make people sick. We are an island in the middle of nowhere. We want to go out to the countryside and breathe. Pure metal air. We are not your car, metal vulture, don't spit your belly. We are an island in the middle of nowhere. We want to go out to the countryside and breathe."
    },
    {
      "id": 123,
      "start_time": 2806.96,
      "end_time": 2818.857,
      "timestamp": "00:46:46",
      "text": "Rural schools present."
    },
    {
      "id": 124,
      "start_time": 2830.839,
      "end_time": 2838.857,
      "timestamp": "00:47:10",
      "text": "Thank you very much. Good night."
    }
  ]
}

BLACK_SUMMER = {
  "metadata": {
    "title": "Black Summer - Australia Bushfire Documentary",
    "total_duration_seconds": 1555.84,
    "format": "Each segment has start_time, end_time (in seconds), timestamp (HH:MM:SS), and text"
  },
  "segments": [
    {
      "id": 1,
      "start_time": 6.64,
      "end_time": 17.2,
      "timestamp": "00:00:06",
      "text": "If you're in the red bow area you need to be seeking shelter as the fire approaches and protecting yourself from the heat of the fire by sheltering in a solid structure."
    },
    {
      "id": 2,
      "start_time": 34.079,
      "end_time": 42.28,
      "timestamp": "00:00:34",
      "text": "They suspected it's going to come towards Gregor. Yeah. Yeah okay."
    },
    {
      "id": 3,
      "start_time": 83.84,
      "end_time": 98.759,
      "timestamp": "00:01:23",
      "text": "Who knows why people need to see things to believe it. The thing is, it's really hard to show climate change. What does climate change look like?"
    },
    {
      "id": 4,
      "start_time": 124.159,
      "end_time": 143.599,
      "timestamp": "00:02:04",
      "text": "Hey Nick, I'm just leaving town now okay. I got another warning light and um yeah, I'll um give you a call once I get to the woman case road. Okay good luck too."
    },
    {
      "id": 5,
      "start_time": 144.16,
      "end_time": 181.0,
      "timestamp": "00:02:24",
      "text": "I've been broke for like at least 10 years, I've just struggled. It's only in the last four or five years that I've been able to make a career out of it and to be able to pay my rent. Yeah, this is my first fire season really. There's a couple days where I was really struggling, you know, breathing wise. On this one day I came home, it's like I had a brick in my lungs, it was really scary to be honest. But I just felt like it had to be — I had to keep working because it's a huge story. So there's no — I didn't have a choice."
    },
    {
      "id": 6,
      "start_time": 204.799,
      "end_time": 229.4,
      "timestamp": "00:03:24",
      "text": "That does look solid yeah. So that's the wind's going this way. We're in um, we're in actually great position right now. Oh here we go. Okay get ready."
    },
    {
      "id": 7,
      "start_time": 302.8,
      "end_time": 329.4,
      "timestamp": "00:05:02",
      "text": "And they've been put out here now so it's probably trying to move on. Do you know those other properties that have been lost or just this one? That was that house and half hour again. We tried. Uh, it looked like you did a good job. It's just the one house, that's all right. Yeah. All righty, good luck, see you around."
    },
    {
      "id": 8,
      "start_time": 345.68,
      "end_time": 413.319,
      "timestamp": "00:05:45",
      "text": "It doesn't really — you know it doesn't feel like what you would think it would feel. Yeah. This is probably the most extreme situation I've been involved in in the last few months. This was a real sort of wake-up call for me, a real lesson. Quite late in the evening and the fire was just bubbling behind these trees and everyone was pretty relaxed. That to me was a real lesson in how fires move and how quickly things can change. We were photographing these fires for two months but it still wasn't at a scale that was shocking people."
    },
    {
      "id": 9,
      "start_time": 426.8,
      "end_time": 440.039,
      "timestamp": "00:07:06",
      "text": "Like Congola, it felt like a front line. The whole order of things has been totally thrown in the window. The neighbors on each side were trying to defend their properties."
    },
    {
      "id": 10,
      "start_time": 447.039,
      "end_time": 460.039,
      "timestamp": "00:07:27",
      "text": "Then I see this kangaroo hopping towards me. Just ran right in front of this one home that was burning."
    },
    {
      "id": 11,
      "start_time": 466.4,
      "end_time": 513.84,
      "timestamp": "00:07:46",
      "text": "I woke up in the morning on the 2nd of January. The image was on nearly every front page of the UK papers and being shared across social media like wildfire. Been featured on many front pages around the world and was taken by the photojournalist Matthew Abbott. He tweeted 'my last day of the decade felt like the apocalypse'. For the rest of the world it was a clear cut climate change induced catastrophe. The fires arrived earlier and with more fury than normal. Australian Prime Minister Scott Morrison countered claims it's a consequence of man-made climate change. Homes have been lost in New South Wales, many with nothing left to say but themselves."
    },
    {
      "id": 12,
      "start_time": 529.279,
      "end_time": 599.32,
      "timestamp": "00:08:49",
      "text": "So we had the front door here yeah, the stairs went up, went up to that stair, went up to a timber lot. Well he's still a bit emotional about it. Yeah, I can't thank you enough for those photos. It's just yeah it's just nice to look at them when you're wanting to think of something or remember it. Were you down at the beach as well? Ran dark so quickly. Yeah, just because I wanted to show everybody that was um — so we actually walked to the um the sand bar because we didn't want to be near the bush. And did you know what happened to the home at that point? No, no we had no contact with the boys either. You didn't even know they were all right. God that's been terrifying."
    },
    {
      "id": 13,
      "start_time": 613.2,
      "end_time": 631.399,
      "timestamp": "00:10:13",
      "text": "We've had fires before but there's nothing like this. The image which now I can't get out of my mind is burning wallabies desperately running to find water to immerse themselves in. They would have perished at any rate but by running they're actually accelerating the spread of the fire."
    },
    {
      "id": 14,
      "start_time": 636.16,
      "end_time": 650.6,
      "timestamp": "00:10:36",
      "text": "Then the black — you died down here, it was scary just going down there. One little dozer track coming out, that's his only access. He was found dead at the front door."
    },
    {
      "id": 15,
      "start_time": 686.959,
      "end_time": 699.84,
      "timestamp": "00:11:26",
      "text": "Between climate change and the region. Hey Nick. No, what's happening? Are you serious? So say that again."
    },
    {
      "id": 16,
      "start_time": 713.279,
      "end_time": 724.7,
      "timestamp": "00:11:53",
      "text": "Okay this is huge. So they've lost like a Boeing — you know, 737's crash into a mountain."
    },
    {
      "id": 17,
      "start_time": 724.88,
      "end_time": 765.399,
      "timestamp": "00:12:04",
      "text": "Hey, how you going? Good. Awesome, awesome. Um, I'm heading to uh Kuma now, where close to where this plane has crashed. Okay, um, which means that there's a good chance I'm not going to be home tonight. Is that okay? I can't remember if we had any plans. Okay sorry, I just started finding out now. It's totally fine, okay, don't worry. But this plane looks like it was um an American plane with American crew. I don't know — I mean it's confirmed it's crashed but I don't know any other details. Yeah look after yourself, you take care now."
    },
    {
      "id": 18,
      "start_time": 784.399,
      "end_time": 807.8,
      "timestamp": "00:13:04",
      "text": "As soon as possible. Oh here we go. It's a copper. They're over there. Yeah. Oh good. Head down. Hang on."
    },
    {
      "id": 19,
      "start_time": 819.12,
      "end_time": 834.24,
      "timestamp": "00:13:39",
      "text": "We were speaking with the same guys, the hazmat team before, and they seemed pretty cool with us being able to head down. What are we — shut down? Like we're with the press. So it's over crime scene, that's all, the crime scene."
    },
    {
      "id": 20,
      "start_time": 835.92,
      "end_time": 843.839,
      "timestamp": "00:13:55",
      "text": "Could have just gotten past him. Like hungry, stressed, tired."
    },
    {
      "id": 21,
      "start_time": 853.68,
      "end_time": 866.36,
      "timestamp": "00:14:13",
      "text": "Killing three crew members. Authorities lost contact with the air tanker while it was passing through the Snowy Monaro region. Aircraft has been contracted from an American company."
    },
    {
      "id": 22,
      "start_time": 909.519,
      "end_time": 942.8,
      "timestamp": "00:15:09",
      "text": "I've also found out I've got a baby on the way. You know, to be bringing a new life into the world when our future is so uncertain, it definitely makes you think about those things."
    },
    {
      "id": 23,
      "start_time": 944.959,
      "end_time": 984.88,
      "timestamp": "00:15:44",
      "text": "So this is what she'd be doing with mom — mom would be fast asleep but she'd get out of the pouch and just sit on her belly and just do this. You know something's here, don't you. It'd be nice. Wombats come on — sweat. They kind of can't sweat. Can't sweat no. So he kills him. What would you do in a fire?"
    },
    {
      "id": 24,
      "start_time": 961.68,
      "end_time": 1038.88,
      "timestamp": "00:16:01",
      "text": "What I find hard, for someone who's actually been in the fire grounds — um, majority of things burned. My business partner Phil, he does all the euthanasing. So he gets called a lot by the wildlife groups to euthanase sick wombats. But most of those are getting put down because there's nowhere for them to go. A lot of wombats have survived but will they survive until it regenerates to actually be self-sufficient again as a natural bushland? I don't know."
    },
    {
      "id": 25,
      "start_time": 1051.679,
      "end_time": 1067.799,
      "timestamp": "00:17:31",
      "text": "If you live in the areas around Breadboa, you are at risk. It is too late to leave. Fire conditions today are erratic and volatile. This is at a scale that we can't even imagine."
    },
    {
      "id": 26,
      "start_time": 1069.679,
      "end_time": 1086.96,
      "timestamp": "00:17:49",
      "text": "The challenge is getting people to engage, make people realize and make people care about climate change and have that emotional connection. I want people to see these things for themselves and make up their own minds."
    },
    {
      "id": 27,
      "start_time": 1091.84,
      "end_time": 1134.52,
      "timestamp": "00:18:11",
      "text": "I recommend with you guys — the road's about to be closed here so you're going to want to — when you're coming out, head back towards red vote to stay in the fire zone start. Stay in Breadboard would be my suggestion. Yeah, fire is likely going to impact through there. Yeah. But going any further north from here, yeah, you're putting yourself in substantial danger. Okay. So he wants us to go — to get out of here. Oh we should go into it. We could come in behind, we could come in behind it though, right? Like if it's coming towards us we'll just keep edging back towards Breadboard. Do you think we should go back the way we came on the highway and punch? Yeah let's see that, let's do that before the road closes. Here, get ready."
    },
    {
      "id": 28,
      "start_time": 1144.32,
      "end_time": 1149.16,
      "timestamp": "00:19:04",
      "text": "Potentially you can get crazy racing."
    },
    {
      "id": 29,
      "start_time": 1158.559,
      "end_time": 1177.36,
      "timestamp": "00:19:18",
      "text": "I'll be very careful here what we do. There's fire there, it's good. Okay. Be careful here. Like cops are there, RFS. All right there."
    },
    {
      "id": 30,
      "start_time": 1491.679,
      "end_time": 1500.52,
      "timestamp": "00:24:51",
      "text": "It's inevitable that we will experience more extreme events like Black Summer in the future."
    },
    {
      "id": 31,
      "start_time": 1500.72,
      "end_time": 1518.799,
      "timestamp": "00:25:00",
      "text": "That's confronting. But we can't turn away from this reality. I want people to engage with that and to be challenged by that."
    },
    {
      "id": 32,
      "start_time": 1512.24,
      "end_time": 1527.64,
      "timestamp": "00:25:12",
      "text": "To think — what if that was me? What if that was my family?"
    },
    {
      "id": 33,
      "start_time": 1519.76,
      "end_time": 1547.64,
      "timestamp": "00:25:19",
      "text": "To make people feel — that's all you can ask for. And the rest is up to them."
    }
  ]
}

MANILA_LOCKDOWN = {
  "metadata": {
    "title": "COVID-19 Philippines Lockdown Documentary",
    "total_duration_seconds": 1501.84,
    "format": "Each segment has start_time, end_time (in seconds), timestamp (HH:MM:SS), and text. Non-speech segments (music, applause, laughter) have been omitted."
  },
  "segments": [
    {
      "id": 1,
      "start_time": 18.0,
      "end_time": 24.48,
      "timestamp": "00:00:18",
      "text": "Chinese from entering. The answer of course is no."
    },
    {
      "id": 2,
      "start_time": 24.8,
      "end_time": 33.95,
      "timestamp": "00:00:24",
      "text": "China has been kind to us. We can only also share the same paper."
    },
    {
      "id": 3,
      "start_time": 126.32,
      "end_time": 132.56,
      "timestamp": "00:02:06",
      "text": "1997."
    },
    {
      "id": 4,
      "start_time": 137.76,
      "end_time": 148.16,
      "timestamp": "00:02:17",
      "text": "I have come to the conclusion that stricter measures are necessary. I am placing the entire mainland of the sun under quarantine."
    },
    {
      "id": 5,
      "start_time": 152.56,
      "end_time": 167.599,
      "timestamp": "00:02:32",
      "text": "The Philippine capital Manila has begun a month's lockdown. Manila's 12 million residents will be under a nightly curfew and are encouraged to stay at home during the day. Most government jobs will be suspended and large gatherings are to be banned."
    },
    {
      "id": 6,
      "start_time": 167.599,
      "end_time": 177.879,
      "timestamp": "00:02:47",
      "text": "The Philippines has taken some of the toughest measures yet in the region, and while people's movement is being restricted, officials hope it will prevent a bigger spread of the virus."
    },
    {
      "id": 7,
      "start_time": 243.04,
      "end_time": 249.04,
      "timestamp": "00:04:13",
      "text": "You're alone."
    },
    {
      "id": 8,
      "start_time": 528.24,
      "end_time": 534.6,
      "timestamp": "00:08:48",
      "text": "A high rate of contamination."
    },
    {
      "id": 9,
      "start_time": 689.76,
      "end_time": 698.56,
      "timestamp": "00:11:29",
      "text": "The 100 billion pesos for one month. All the 270 billion pesos for two months is not enough."
    },
    {
      "id": 10,
      "start_time": 709.67,
      "end_time": 715.06,
      "timestamp": "00:11:49",
      "text": "I'm calling on the Secretary of Finance to generate a producer."
    },
    {
      "id": 11,
      "start_time": 773.52,
      "end_time": 782.0,
      "timestamp": "00:12:53",
      "text": "In the margins and the vulnerable groups."
    },
    {
      "id": 12,
      "start_time": 1340.559,
      "end_time": 1357.18,
      "timestamp": "00:22:20",
      "text": "My orders. Them dead."
    },
    {
      "id": 13,
      "start_time": 1499.76,
      "end_time": 1501.84,
      "timestamp": "00:24:59",
      "text": "You."
    }
  ]
}

# ---------------------------------------------------------------------------

VIDEO_LIBRARY: dict[int, tuple[str, dict]] = {
    1: ("Climate Change Explainer", CLIMATE_CHANGE),
    2: ("Urgent Songs for My Land", A_SONG_FOR_MY_LAND),
    3: ("Black Summer - Australia Bushfire Documentary", BLACK_SUMMER),
    4: ("COVID-19 Philippines Lockdown Documentary", MANILA_LOCKDOWN),
}

VIDEO_S3_KEYS_BY_TITLE: dict[str, str] = {
    "Climate Change Explainer": os.environ.get(
        "S3_KEY_CLIMATE_CHANGE", "climatechangemodified.mp4"
    ),
    "Urgent Songs for My Land": os.environ.get(
        "S3_KEY_URGENT_SONGS",
        "YTDown.com_YouTube_Musical-resistance-in-Argentina-Children_Media_9Lc2X10tjPw_002_720p.mp4",
    ),
    "Black Summer - Australia Bushfire Documentary": os.environ.get(
        "S3_KEY_BLACK_SUMMER",
        "Blacksummer.mp4",
    ),
    "COVID-19 Philippines Lockdown Documentary": os.environ.get(
        "S3_KEY_MANILA_LOCKDOWN",
        "Manila lockdown.mp4",
    ),
}

SOURCE_BUCKET = os.environ.get("SOURCE_BUCKET", "genaifoundryc-y2t1oh")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "genaifoundryc-y2t1oh")

S3_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
s3_client = boto3.client("s3", region_name=S3_REGION)

# ---------------------------------------------------------------------------
# Bedrock / clip-retrieval
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a precise video segment retrieval assistant. You are given subtitle data from one or more videos. Each segment has an id, start_time, end_time (in seconds), and text.

When the user asks a question or describes content they want, you must:
1. Identify all relevant segments from the subtitle data that match the query
2. Merge consecutive or near-consecutive segments (within 2 seconds gap) into a single clip range
3. If the user specifies a target duration for the final video, select and prioritize clips so that their TOTAL combined duration fits within that target duration
4. When trimming is needed to fit the target duration, KEEP the clips with the most context, narrative value, or relevance — drop the weakest/most redundant ones first
5. Return ONLY a valid JSON array — no explanation, no markdown, no extra text

Target duration rules (when user specifies a time):
- Calculate each clip's duration as (end_time - start_time)
- Sum of all clip durations must NOT exceed the target duration in seconds
- If all matching clips fit within the target duration, include all of them
- If clips exceed the target duration, rank them by contextual importance and drop the least important ones until the total fits
- Never cut a clip short mid-sentence to fit — drop the whole clip instead even if it exceeds the target duration slightly

Output format:
[
  {{
    "video": "<video_title>",
    "clip_index": 1,
    "start_time": <float>,
    "end_time": <float>,
    "start_timestamp": "HH:MM:SS",
    "end_timestamp": "HH:MM:SS",
    "matched_segment_ids": [1, 2, 3],
    "description": "<brief reason why this clip was selected>"
  }},
  {{
    "video": "<video_title>",
    "clip_index": 2,
    "start_time": <float>,
    "end_time": <float>,
    "start_timestamp": "HH:MM:SS",
    "end_timestamp": "HH:MM:SS",
    "matched_segment_ids": [4, 5, 6],
    "description": "<brief reason why this clip was selected>"
  }},   
  ...
]

Rules:
- start_time and end_time must be floats in seconds (directly usable by ffmpeg)
- If multiple consecutive segments match, merge them into one clip with the earliest start_time and latest end_time
- If no segments match, return an empty array: []
- Never hallucinate timestamps — only use exact values from the provided subtitle data
- clip_index increments per video independently
- If no target duration is specified by the user, include ALL matching clips and still include the summary object"""


def get_video_clips(user_query: str, videos: dict, video_time: float) -> list:
    """
    Args:
        user_query: Natural language query describing the content to find
        videos: Dict of {"video_title": subtitle_json, ...}
    Returns:
        List of clip dicts with start/end times
    """
    try:
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

        subtitle_context = ""
        for title, data in videos.items():
            subtitle_context += f"\n\n=== VIDEO: {title} ===\n"
            subtitle_context += json.dumps(data, indent=2)

        user_message = f"""Here is the subtitle data for the available videos:
    {subtitle_context}

    User query: {user_query}
    Video time: {video_time}

    Return the JSON array of matching clip ranges.
    The video time is the total duration of the video in seconds."""

        response = bedrock.invoke_model(
            modelId="global.anthropic.claude-sonnet-4-20250514-v1:0",
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1024,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_message}],
                }
            ),
            contentType="application/json",
            accept="application/json",
        )

        result = json.loads(response["body"].read())
        print(result)
        raw_text = result["content"][0]["text"].strip()

        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        return json.loads(raw_text.strip())
    except Exception as e:
        print(f"Error getting video clips: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# S3 / video helpers
# ---------------------------------------------------------------------------


def download_from_s3(bucket: str, key: str, local_path: str) -> None:
    print(f"Downloading s3://{bucket}/{key} -> {local_path}")
    s3_client.download_file(bucket, key, local_path)


def _get_ffmpeg_path() -> str:
    """Resolve the ffmpeg binary — prefers imageio-bundled build, falls back to system PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return os.environ.get("FFMPEG_BINARY", "ffmpeg")


def cut_and_merge(clips: list, local_input_files: dict, output_path: str) -> None:
    """
    Cut clips from local video files and merge them into one output using ffmpeg
    directly — avoids the slow Python frame-by-frame loop of moviepy entirely.

    clips: output from get_video_clips()
    local_input_files: {"Climate Change Explainer": "/tmp/climate.mp4", ...}
    output_path: local path for the merged output
    """
    ffmpeg = _get_ffmpeg_path()
    print(f"Using ffmpeg at: {ffmpeg}")

    segment_files: list[str] = []

    # ── Step 1: Cut each clip ─────────────────────────────────────────────────
    for i, clip in enumerate(clips):
        title = clip["video"]
        input_path = local_input_files[title]
        segment_out = f"/tmp/segment_{i}_{uuid.uuid4().hex[:6]}.mp4"
        duration = clip["end_time"] - clip["start_time"]

        print(
            f"Cutting clip {i + 1}: {clip['start_timestamp']} -> {clip['end_timestamp']}"
            f" ({duration:.1f}s) from {title}"
        )

        result = subprocess.run(
            [
                ffmpeg, "-y",
                "-ss", str(clip["start_time"]),
                "-i", input_path,
                "-t", str(duration),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-c:a", "aac",
                "-b:a", "128k",
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                segment_out,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"STDERR: {result.stderr[-1000:]}")
            raise RuntimeError(f"ffmpeg cut failed on clip {i + 1}: {result.stderr[-300:]}")

        segment_files.append(segment_out)
        print(f"Segment {i + 1} written: {segment_out}")

    if not segment_files:
        raise ValueError("No segments were produced")

    # ── Step 2: Write concat manifest ────────────────────────────────────────
    concat_list = f"/tmp/concat_{uuid.uuid4().hex[:6]}.txt"
    with open(concat_list, "w") as f:
        for seg in segment_files:
            f.write(f"file '{seg}'\n")

    # ── Step 3: Merge all segments ───────────────────────────────────────────
    print(f"Merging {len(segment_files)} segment(s) into {output_path}")
    result = subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"STDERR: {result.stderr[-1000:]}")
        raise RuntimeError(f"ffmpeg merge failed: {result.stderr[-300:]}")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    for seg in segment_files:
        if os.path.exists(seg):
            os.remove(seg)
    if os.path.exists(concat_list):
        os.remove(concat_list)

    print(f"Output written to {output_path}")


def upload_to_s3(local_path: str, bucket: str, key: str) -> None:
    print(f"Uploading {local_path} -> s3://{bucket}/{key}")
    s3_client.upload_file(
        local_path,
        bucket,
        key,
        ExtraArgs={"ContentType": "video/mp4"},
    )


def generate_presigned_url(bucket: str, key: str, expiry_seconds: int = 3600) -> str:
    return s3_client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{os.path.basename(key)}"',
        },
        ExpiresIn=expiry_seconds,
    )


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------


def process_video_query(
    user_query: str,
    video_id: int,
    source_bucket: str,
    output_bucket: str,
    output_key_prefix: str = "outputs/",
    presigned_expiry: int = 3600,
    video_time: float = 0.0,
) -> dict:
    entry = VIDEO_LIBRARY.get(video_id)
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Unknown video_id {video_id}",
                "allowed_video_ids": list(VIDEO_LIBRARY.keys()),
            },
        )

    title, subtitle_data = entry
    clips = get_video_clips(user_query, {title: subtitle_data}, video_time)

    if not clips:
        raise HTTPException(
            status_code=404,
            detail={"error": "No matching clips found", "query": user_query, "video_id": video_id},
        )

    print(f"Found {len(clips)} clip(s)")

    needed_titles = {clip["video"] for clip in clips}
    local_input_files: dict[str, str] = {}

    for video_title in needed_titles:
        s3_key = VIDEO_S3_KEYS_BY_TITLE[video_title]
        local_path = f"/tmp/{os.path.basename(s3_key)}"
        download_from_s3(source_bucket, s3_key, local_path)
        local_input_files[video_title] = local_path

    job_id = uuid.uuid4().hex[:8]
    output_filename = f"merged_{job_id}.mp4"
    local_output_path = f"/tmp/{output_filename}"

    cut_and_merge(clips, local_input_files, local_output_path)

    output_s3_key = f"{output_key_prefix.rstrip('/')}/{output_filename}"
    upload_to_s3(local_output_path, output_bucket, output_s3_key)

    presigned_url = generate_presigned_url(output_bucket, output_s3_key, presigned_expiry)

    for local_path in local_input_files.values():
        if os.path.exists(local_path):
            os.remove(local_path)
    if os.path.exists(local_output_path):
        os.remove(local_output_path)

    return {
        "query": user_query,
        "video_id": video_id,
        "video_title": title,
        "download_url": presigned_url,
        "expires_in_seconds": presigned_expiry,
        "clips_used": clips,
        "total_clips": len(clips),
        "output_s3_key": output_s3_key,
    }


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Video Edit Service", version="1.0.0")


class VideoEditRequest(BaseModel):
    query: str
    video_time: int
    media_id: int
    source_bucket: str = Field(default_factory=lambda: SOURCE_BUCKET)
    output_bucket: str = Field(default_factory=lambda: OUTPUT_BUCKET)
    output_key_prefix: str = "outputs/"
    presigned_expiry: int = Field(
        default_factory=lambda: int(os.environ.get("PRESIGNED_EXPIRY", "3600"))
    )


class VideoEditResponse(BaseModel):
    query: str
    video_id: int
    video_title: str
    download_url: str
    expires_in_seconds: int
    clips_used: list
    total_clips: int
    output_s3_key: str

origins = ["*"]
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Allows configured origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


@app.post("/edit-video", response_model=VideoEditResponse)
def edit_video(request: VideoEditRequest) -> VideoEditResponse:
    """
    Receive a video edit request, find matching clips via Bedrock,
    cut & merge them, upload the result to S3, and return a presigned URL.
    """
    result = process_video_query(
        user_query=request.query,
        video_id=request.media_id,
        source_bucket=request.source_bucket,
        output_bucket=request.output_bucket,
        output_key_prefix=request.output_key_prefix,
        presigned_expiry=request.presigned_expiry,
        video_time=request.video_time,
    )
    return VideoEditResponse(**result)


if __name__ == "__main__":
    uvicorn.run("edit_video:app", host="0.0.0.0", port=8000, reload=False)
