--
-- PostgreSQL database dump
--

-- Dumped from database version 17.4
-- Dumped by pg_dump version 17.3

-- Started on 2025-08-04 15:39:25

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- TOC entry 7 (class 2615 OID 17050)
-- Name: foundry_app; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA foundry_app;


ALTER SCHEMA foundry_app OWNER TO postgres;

--
-- TOC entry 6 (class 2615 OID 16478)
-- Name: genaifoundry; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA genaifoundry;


ALTER SCHEMA genaifoundry OWNER TO postgres;

--
-- TOC entry 4 (class 2615 OID 2200)
-- Name: public; Type: SCHEMA; Schema: -; Owner: pg_database_owner
--

CREATE SCHEMA public;


ALTER SCHEMA public OWNER TO pg_database_owner;

--
-- TOC entry 4401 (class 0 OID 0)
-- Dependencies: 4
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: pg_database_owner
--

COMMENT ON SCHEMA public IS 'standard public schema';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- TOC entry 237 (class 1259 OID 17059)
-- Name: cust_upload; Type: TABLE; Schema: foundry_app; Owner: postgres
--

CREATE TABLE foundry_app.cust_upload (
    eventid character varying,
    custid character varying,
    custname character varying,
    custcompany character varying,
    id integer NOT NULL,
    insertedon timestamp without time zone
);


ALTER TABLE foundry_app.cust_upload OWNER TO postgres;

--
-- TOC entry 236 (class 1259 OID 17058)
-- Name: cust_upload_id_seq; Type: SEQUENCE; Schema: foundry_app; Owner: postgres
--

CREATE SEQUENCE foundry_app.cust_upload_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE foundry_app.cust_upload_id_seq OWNER TO postgres;

--
-- TOC entry 4402 (class 0 OID 0)
-- Dependencies: 236
-- Name: cust_upload_id_seq; Type: SEQUENCE OWNED BY; Schema: foundry_app; Owner: postgres
--

ALTER SEQUENCE foundry_app.cust_upload_id_seq OWNED BY foundry_app.cust_upload.id;


--
-- TOC entry 235 (class 1259 OID 17052)
-- Name: eventinfo; Type: TABLE; Schema: foundry_app; Owner: postgres
--

CREATE TABLE foundry_app.eventinfo (
    id integer NOT NULL,
    eventid character varying,
    eventname character varying,
    eventdesc character varying,
    eventdate date,
    eventtime time without time zone,
    eventlocation character varying,
    eventstatus character varying,
    insertedon timestamp without time zone
);


ALTER TABLE foundry_app.eventinfo OWNER TO postgres;

--
-- TOC entry 234 (class 1259 OID 17051)
-- Name: eventinfo_id_seq; Type: SEQUENCE; Schema: foundry_app; Owner: postgres
--

CREATE SEQUENCE foundry_app.eventinfo_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE foundry_app.eventinfo_id_seq OWNER TO postgres;

--
-- TOC entry 4403 (class 0 OID 0)
-- Dependencies: 234
-- Name: eventinfo_id_seq; Type: SEQUENCE OWNED BY; Schema: foundry_app; Owner: postgres
--

ALTER SEQUENCE foundry_app.eventinfo_id_seq OWNED BY foundry_app.eventinfo.id;


--
-- TOC entry 227 (class 1259 OID 16872)
-- Name: audio_summary; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.audio_summary (
    id integer NOT NULL,
    audio text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    session_id character varying(255)
);


ALTER TABLE genaifoundry.audio_summary OWNER TO postgres;

--
-- TOC entry 226 (class 1259 OID 16871)
-- Name: audio_summary_id_seq; Type: SEQUENCE; Schema: genaifoundry; Owner: postgres
--

CREATE SEQUENCE genaifoundry.audio_summary_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE genaifoundry.audio_summary_id_seq OWNER TO postgres;

--
-- TOC entry 4404 (class 0 OID 0)
-- Dependencies: 226
-- Name: audio_summary_id_seq; Type: SEQUENCE OWNED BY; Schema: genaifoundry; Owner: postgres
--

ALTER SEQUENCE genaifoundry.audio_summary_id_seq OWNED BY genaifoundry.audio_summary.id;


--
-- TOC entry 232 (class 1259 OID 17016)
-- Name: bank_voice_history; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.bank_voice_history (
    id integer,
    session_id character varying(50),
    question character varying(128),
    answer character varying(2048),
    input_tokens integer,
    output_tokens integer,
    created_on character varying(50),
    updated_on character varying(50)
);


ALTER TABLE genaifoundry.bank_voice_history OWNER TO postgres;

--
-- TOC entry 231 (class 1259 OID 16986)
-- Name: banking_chat_history; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.banking_chat_history (
    id integer,
    session_id character varying(50),
    question character varying(128),
    answer character varying(2048),
    input_tokens integer,
    output_tokens integer,
    created_on character varying(50),
    updated_on character varying(50)
);


ALTER TABLE genaifoundry.banking_chat_history OWNER TO postgres;

--
-- TOC entry 219 (class 1259 OID 16479)
-- Name: ce_cexp_logs; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.ce_cexp_logs (
    id integer,
    created_on character varying(50),
    environment character varying(50),
    session_time character varying(50),
    lead integer,
    enquiry integer,
    complaint integer,
    summary character varying(1024),
    whatsapp_content character varying(2048),
    next_best_action character varying(1024),
    session_id character varying(50),
    lead_explanation character varying(1024),
    sentiment character varying(50),
    sentiment_explanation character varying(512),
    connectionid character varying(50),
    input_token integer,
    output_token integer,
    topic character varying(50)
);


ALTER TABLE genaifoundry.ce_cexp_logs OWNER TO postgres;

--
-- TOC entry 220 (class 1259 OID 16735)
-- Name: chat_history; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.chat_history (
    id integer,
    session_id character varying(50),
    question character varying(128),
    answer character varying(2048),
    input_tokens integer,
    output_tokens integer,
    created_on character varying(50),
    updated_on character varying(50)
);


ALTER TABLE genaifoundry.chat_history OWNER TO postgres;

--
-- TOC entry 221 (class 1259 OID 16753)
-- Name: chat_table; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.chat_table (
    id integer,
    session_id character varying(50),
    conversation_id character varying(50),
    type character varying(50),
    message character varying(512),
    inserted_on character varying(50),
    inserted_by character varying(50),
    updated_on character varying(50),
    updated_by character varying(50)
);


ALTER TABLE genaifoundry.chat_table OWNER TO postgres;

--
-- TOC entry 225 (class 1259 OID 16864)
-- Name: pca_summary; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.pca_summary (
    session_id character varying(255) NOT NULL,
    language character varying(50),
    trans_type character varying(50),
    transcript jsonb,
    summary text,
    time_stamp timestamp without time zone
);


ALTER TABLE genaifoundry.pca_summary OWNER TO postgres;

--
-- TOC entry 222 (class 1259 OID 16758)
-- Name: post_chat; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.post_chat (
    id integer,
    username character varying(50),
    session_id character varying(50),
    details character varying(512),
    insertedon character varying(50)
);


ALTER TABLE genaifoundry.post_chat OWNER TO postgres;

--
-- TOC entry 223 (class 1259 OID 16763)
-- Name: prompt_metadata; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.prompt_metadata (
    id integer,
    base_prompt character varying,
    analytics_prompt character varying(8192)
);


ALTER TABLE genaifoundry.prompt_metadata OWNER TO postgres;

--
-- TOC entry 238 (class 1259 OID 17066)
-- Name: retail_chat_history; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.retail_chat_history (
    id integer,
    session_id character varying(50),
    question character varying(128),
    answer character varying(2048),
    input_tokens integer,
    output_tokens integer,
    created_on character varying(50),
    updated_on character varying(50)
);


ALTER TABLE genaifoundry.retail_chat_history OWNER TO postgres;

--
-- TOC entry 229 (class 1259 OID 16887)
-- Name: transcript; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.transcript (
    id integer NOT NULL,
    transcript_json jsonb,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    session_id character varying(255)
);


ALTER TABLE genaifoundry.transcript OWNER TO postgres;

--
-- TOC entry 228 (class 1259 OID 16886)
-- Name: transcript_id_seq; Type: SEQUENCE; Schema: genaifoundry; Owner: postgres
--

CREATE SEQUENCE genaifoundry.transcript_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE genaifoundry.transcript_id_seq OWNER TO postgres;

--
-- TOC entry 4405 (class 0 OID 0)
-- Dependencies: 228
-- Name: transcript_id_seq; Type: SEQUENCE OWNED BY; Schema: genaifoundry; Owner: postgres
--

ALTER SEQUENCE genaifoundry.transcript_id_seq OWNED BY genaifoundry.transcript.id;


--
-- TOC entry 224 (class 1259 OID 16770)
-- Name: user_table; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.user_table (
    id character varying(50),
    user_id character varying(50),
    session_id character varying(50),
    inserted_on character varying(50),
    inserted_by character varying(50),
    updated_on character varying(50),
    updated_by character varying(50)
);


ALTER TABLE genaifoundry.user_table OWNER TO postgres;

--
-- TOC entry 239 (class 1259 OID 17108)
-- Name: vid_gen_link; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.vid_gen_link (
    session_id character varying NOT NULL,
    s3_link character varying
);


ALTER TABLE genaifoundry.vid_gen_link OWNER TO postgres;

--
-- TOC entry 230 (class 1259 OID 16943)
-- Name: voice_history; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.voice_history (
    id integer,
    session_id character varying(50),
    question character varying(128),
    answer character varying(2048),
    input_tokens integer,
    output_tokens integer,
    created_on character varying(50),
    updated_on character varying(50)
);


ALTER TABLE genaifoundry.voice_history OWNER TO postgres;

--
-- TOC entry 233 (class 1259 OID 17024)
-- Name: voicebot_meta; Type: TABLE; Schema: genaifoundry; Owner: postgres
--

CREATE TABLE genaifoundry.voicebot_meta (
    id integer,
    usecase character varying,
    kb_id character varying,
    prompt_template character varying,
    table_name character varying
);


ALTER TABLE genaifoundry.voicebot_meta OWNER TO postgres;

--
-- TOC entry 4221 (class 2604 OID 17062)
-- Name: cust_upload id; Type: DEFAULT; Schema: foundry_app; Owner: postgres
--

ALTER TABLE ONLY foundry_app.cust_upload ALTER COLUMN id SET DEFAULT nextval('foundry_app.cust_upload_id_seq'::regclass);


--
-- TOC entry 4220 (class 2604 OID 17055)
-- Name: eventinfo id; Type: DEFAULT; Schema: foundry_app; Owner: postgres
--

ALTER TABLE ONLY foundry_app.eventinfo ALTER COLUMN id SET DEFAULT nextval('foundry_app.eventinfo_id_seq'::regclass);


--
-- TOC entry 4216 (class 2604 OID 16875)
-- Name: audio_summary id; Type: DEFAULT; Schema: genaifoundry; Owner: postgres
--

ALTER TABLE ONLY genaifoundry.audio_summary ALTER COLUMN id SET DEFAULT nextval('genaifoundry.audio_summary_id_seq'::regclass);


--
-- TOC entry 4218 (class 2604 OID 16890)
-- Name: transcript id; Type: DEFAULT; Schema: genaifoundry; Owner: postgres
--

ALTER TABLE ONLY genaifoundry.transcript ALTER COLUMN id SET DEFAULT nextval('genaifoundry.transcript_id_seq'::regclass);


--
-- TOC entry 4393 (class 0 OID 17059)
-- Dependencies: 237
-- Data for Name: cust_upload; Type: TABLE DATA; Schema: foundry_app; Owner: postgres
--

--
-- TOC entry 4406 (class 0 OID 0)
-- Dependencies: 236
-- Name: cust_upload_id_seq; Type: SEQUENCE SET; Schema: foundry_app; Owner: postgres
--

SELECT pg_catalog.setval('foundry_app.cust_upload_id_seq', 8, true);


--
-- TOC entry 4407 (class 0 OID 0)
-- Dependencies: 234
-- Name: eventinfo_id_seq; Type: SEQUENCE SET; Schema: foundry_app; Owner: postgres
--

SELECT pg_catalog.setval('foundry_app.eventinfo_id_seq', 8, true);


--
-- TOC entry 4408 (class 0 OID 0)
-- Dependencies: 226
-- Name: audio_summary_id_seq; Type: SEQUENCE SET; Schema: genaifoundry; Owner: postgres
--

SELECT pg_catalog.setval('genaifoundry.audio_summary_id_seq', 1, false);


--
-- TOC entry 4409 (class 0 OID 0)
-- Dependencies: 228
-- Name: transcript_id_seq; Type: SEQUENCE SET; Schema: genaifoundry; Owner: postgres
--

SELECT pg_catalog.setval('genaifoundry.transcript_id_seq', 1, false);


--
-- TOC entry 4225 (class 2606 OID 16880)
-- Name: audio_summary audio_summary_pkey; Type: CONSTRAINT; Schema: genaifoundry; Owner: postgres
--

ALTER TABLE ONLY genaifoundry.audio_summary
    ADD CONSTRAINT audio_summary_pkey PRIMARY KEY (id);


--
-- TOC entry 4223 (class 2606 OID 16870)
-- Name: pca_summary pca_summary_pkey; Type: CONSTRAINT; Schema: genaifoundry; Owner: postgres
--

ALTER TABLE ONLY genaifoundry.pca_summary
    ADD CONSTRAINT pca_summary_pkey PRIMARY KEY (session_id);


--
-- TOC entry 4227 (class 2606 OID 16895)
-- Name: transcript transcript_pkey; Type: CONSTRAINT; Schema: genaifoundry; Owner: postgres
--

ALTER TABLE ONLY genaifoundry.transcript
    ADD CONSTRAINT transcript_pkey PRIMARY KEY (id);


--
-- TOC entry 4228 (class 2606 OID 16881)
-- Name: audio_summary audio_summary_session_id_fkey; Type: FK CONSTRAINT; Schema: genaifoundry; Owner: postgres
--

ALTER TABLE ONLY genaifoundry.audio_summary
    ADD CONSTRAINT audio_summary_session_id_fkey FOREIGN KEY (session_id) REFERENCES genaifoundry.pca_summary(session_id);


--
-- TOC entry 4229 (class 2606 OID 16896)
-- Name: transcript transcript_session_id_fkey; Type: FK CONSTRAINT; Schema: genaifoundry; Owner: postgres
--

ALTER TABLE ONLY genaifoundry.transcript
    ADD CONSTRAINT transcript_session_id_fkey FOREIGN KEY (session_id) REFERENCES genaifoundry.pca_summary(session_id);


-- Completed on 2025-08-04 15:39:32

--
-- PostgreSQL database dump complete
--

