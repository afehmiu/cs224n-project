"""Download and pre-process SQuAD and GloVe.

Usage:
    > source activate squad
    > python setup.py

Pre-processing code adapted from:
    > https://github.com/HKUST-KnowComp/R-Net/blob/master/prepro.py

Author:
    Chris Chute (chute@stanford.edu)
"""

"""Adapted from setup.py file, to call tree_parse paraphrases (if appropriate flag set) for use in the baseline model.
1) Run normal setup.py
2) Run normal train.py (trains BiDAF) (takes a long time)

3) Run this setup.py - prepares for paraphraser
4) Run train_paraphraser.py - trains the paraphrase generator using a static BiDAF trained in (2)

"""

import numpy as np
import os
import spacy
import ujson as json

from args import get_setup_args
from codecs import open
from collections import Counter
from tqdm import tqdm

from setup import url_to_data_path, word_tokenize, convert_idx, get_embedding, is_answerable, save, load
from tree_parse import AllenPredictor

from pprint import pprint as pp


# Modified from process_file in setup.py.
#
# We need to get all the possible phrasal replacements
def process_file(filename, data_type, word_counter, char_counter):
    print(f"Pre-processing {data_type} examples...")
    examples = []
    eval_examples = {}
    total = 0
    #paraphrases_generated = 0  was used in baseline_para

    # Process all examples in the json file
    with open(filename, "r") as fh:
        source = json.load(fh)
        for article in tqdm(source["data"]):
            for para in article["paragraphs"]:
                context = para["context"].replace(
                    "''", '" ').replace("``", '" ')
                context_tokens = word_tokenize(context)                     # each word
                context_chars = [list(token) for token in context_tokens]   # char tokens (seem not ever to be used)
                spans = convert_idx(context, context_tokens)                # list of spans for each word

                # for each word, record how frequently it appears (to weight for embedding)
                for token in context_tokens:
                    word_counter[token] += len(para["qas"])                 # num unique QA pairs for this context
                    for char in token:
                        char_counter[char] += len(para["qas"])

                # New for tree paraphraser
                # This is a List of lists, for each phrase type, has list of possible replacement phrases
                context_replacement_phrases = allen_tree.context_to_replacement_phrase_sets(context)
                # print("printing context replacement phrases")
                # pp(context_replacement_phrases)
                context_replacement_tokens = [[p['phrase'] for p in phrase_type] for phrase_type in context_replacement_phrases]
                #print("printing thei representation of context")
                #pp(context_tokens)

                #print("\n\nprinting tokens")
                #pp(context_replacement_tokens)

                # Parse each of the question-answer pairs (qa)
                for qa in para["qas"]:
                    total += 1
                    ques = qa["question"].replace(
                        "''", '" ').replace("``", '" ')
                    ques_tokens = word_tokenize(ques)
                    ques_chars = [list(token) for token in ques_tokens]
                    for token in ques_tokens:
                        word_counter[token] += 1
                        for char in token:
                            char_counter[char] += 1

                    # New for tree paraphraser
                    # this is a list of dicts
                    replace_phrases, _ = allen_tree.sentence_to_phrases(ques, include_non_matching_phrases=True)
                    replace_phrases_tokens = [p["phrase"] for p in replace_phrases]  # (num phrases, num words in phrase)
                    replace_phrase_types = [p["type"] for p in replace_phrases]                     # (num phrases)
                    pp(replace_phrases_tokens)

                    # For each qa, we get all the answers (there are multiple)
                    y1s, y2s = [], []       # accumulate spans of all answer spans (starts and ends)
                    answer_texts = []       # the actual answer texts
                    for answer in qa["answers"]:
                        answer_text = answer["text"]
                        answer_start = answer['answer_start']
                        answer_end = answer_start + len(answer_text)
                        answer_texts.append(answer_text)
                        answer_span = []
                        # If the answer text overlaps with any of the spans, mark the words it contains
                        for idx, span in enumerate(spans):
                            if not (answer_end <= span[0] or answer_start >= span[1]):
                                answer_span.append(idx)
                        y1, y2 = answer_span[0], answer_span[-1]    # start and end indices for the answer span
                        y1s.append(y1)
                        y2s.append(y2)

                    # one for each question
                    # Modified for tree paraphraser
                    example = {"context_tokens": context_tokens,    # for .npz files via build_features
                               "context_chars": context_chars,
                               "ques_tokens": ques_tokens,
                               "ques_chars": ques_chars,
                               "y1s": y1s,
                               "y2s": y2s,
                               "id": total,
                               "context_phrases": context_replacement_tokens,   # List(List(List(words))); phrase type => phrases => words in phrase
                               "ques_phrases": replace_phrases_tokens,          # List(list(words)); phrase => words in phrase
                               "ques_phrase_types": replace_phrase_types        # List(ints); phrase => type of phrase
                               }
                    examples.append(example)

                    # todo: why do they use str(total) instead of just total
                    eval_examples[str(total)] = {"context": context,        # for dev_eval and train_eval
                                                 "question": ques,
                                                 "spans": spans,
                                                 "answers": answer_texts,
                                                 "uuid": qa["id"],
                                                 "context_phrases": context_replacement_tokens,   # List(List(List(words))); phrase type => phrases => words in phrase
                                                 "ques_phrases": replace_phrases_tokens,          # List(list(words)); phrase => words in phrase
                                                 "ques_phrase_types": replace_phrase_types        # List(ints); phrase => type of phrase
                                                 }
                                                  #"paraphrase_id": paraphrase_id}    # new - used for baseline

                    # only one example if it's a short test
                    if args_.short_test:
                        return examples, eval_examples
                    else:
                        print("not short test")

        print(f"{len(examples)} questions in total")
    return examples, eval_examples


def build_features(args, examples, data_type, out_file, word2idx_dict, char2idx_dict, is_test=False):
    para_limit = args.test_para_limit if is_test else args.para_limit
    ques_limit = args.test_ques_limit if is_test else args.ques_limit
    ans_limit = args.ans_limit
    char_limit = args.char_limit

    # New for paraphraser todo
    ques_phrase_limit = ques_limit    # max num phrases in the question (potentially one for each word)
    replacement_phrase_limit = para_limit
    max_phrase_len = args.allen_tree_max_phrase_len
    num_phrase_types = allen_tree.num_replacement_node_types

    def _drop_example(ex, is_test_=False):
        if is_test_:
            drop = False
        else:
            drop = len(ex["context_tokens"]) > para_limit or \
                   len(ex["ques_tokens"]) > ques_limit or \
                   (is_answerable(ex) and
                    ex["y2s"][0] - ex["y1s"][0] > ans_limit)
        return drop

    print(f"Converting {data_type} examples to indices...")
    total = 0
    total_ = 0
    context_idxs = []
    context_char_idxs = []
    ques_idxs = []
    ques_char_idxs = []

    context_phrase_idxs = []
    ques_phrase_idxs = []
    ques_phrase_types = []

    y1s = []
    y2s = []
    ids = []
    for n, example in tqdm(enumerate(examples)):
        total_ += 1

        if _drop_example(example, is_test):
            continue

        total += 1

        def _get_word(word):
            for each in (word, word.lower(), word.capitalize(), word.upper()): # check all possible manipulations
                if each in word2idx_dict:
                    return word2idx_dict[each]
            return 1

        def _get_char(char):
            if char in char2idx_dict:
                return char2idx_dict[char]
            return 1

        context_idx = np.zeros([para_limit], dtype=np.int32)
        context_char_idx = np.zeros([para_limit, char_limit], dtype=np.int32)
        ques_idx = np.zeros([ques_limit], dtype=np.int32)
        ques_char_idx = np.zeros([ques_limit, char_limit], dtype=np.int32)

        # new for paraphrasing (note we don't bother with characters - todo)
        context_phrase_idx = np.zeros([num_phrase_types, replacement_phrase_limit, max_phrase_len]) # 3d
        ques_phrase_idx = np.zeros([ques_phrase_limit, max_phrase_len])     # 2d
        ques_phrase_type = np.zeros([ques_phrase_limit])                    # 1d

        # update words to their index in the embeddings
        for i, token in enumerate(example["context_tokens"]):
            context_idx[i] = _get_word(token)
        context_idxs.append(context_idx)

        # update words (in question) to index in the embeddings
        for i, token in enumerate(example["ques_tokens"]):
            ques_idx[i] = _get_word(token)
        ques_idxs.append(ques_idx)

        # new for paraphrasing
        # update words (in phrases) to index in the embeddings
        for i, q_phrase in enumerate(example["ques_phrases"]):      # (num phrases, words)
            ques_phrase_type[i] = example["ques_phrase_types"][i]   # simultaneously populate types
            for j, word in enumerate(q_phrase):
                ques_phrase_idx[i][j] = _get_word(word)
        ques_phrase_idxs.append(ques_phrase_idx)
        ques_phrase_types.append(ques_phrase_type)

        for i, phrase_type in enumerate(example["context_phrases"]):
            for j, phrase in enumerate(phrase_type):
                for k, word in enumerate(phrase):
                    context_phrase_idx[i][j][k] = _get_word(word)
        context_phrase_idxs.append(context_phrase_idx)

        # same as above but for characters
        # matrix indexed by (word, char in word)
        # todo: we omit the paraphrases here bc this is not used
        for i, token in enumerate(example["context_chars"]):
            for j, char in enumerate(token):
                if j == char_limit:
                    break
                context_char_idx[i, j] = _get_char(char)
        context_char_idxs.append(context_char_idx)

        # same as above but for characters
        for i, token in enumerate(example["ques_chars"]):
            for j, char in enumerate(token):
                if j == char_limit:
                    break
                ques_char_idx[i, j] = _get_char(char)
        ques_char_idxs.append(ques_char_idx)

        if is_answerable(example):
            start, end = example["y1s"][-1], example["y2s"][-1]
        else:
            start, end = -1, -1

        y1s.append(start)
        y2s.append(end)
        ids.append(example["id"])

        pp(ques_phrase_idxs)
        pp(ques_phrase_types)
        pp(context_phrase_idxs)

    np.savez(
        out_file,                                      # saves .npz file which is iterated over in train.py, test.py
        context_idxs=np.array(context_idxs),           # this will save both dev and train
        context_char_idxs=np.array(context_char_idxs),
        ques_idxs=np.array(ques_idxs),
        ques_char_idxs=np.array(ques_char_idxs),
        y1s=np.array(y1s),
        y2s=np.array(y2s),
        context_phrase_idxs = np.array(context_phrase_idxs),    # each time: phrase types, num phrases, num words
        ques_phrase_idxs = np.array(ques_phrase_idxs),          # each item: num phrases in ques, num words
        ques_phrase_types = np.array(ques_phrase_types),
        ids=np.array(ids)
    )
    print(f"Built {total} / {total_} instances of features in total")
    return {"total" : total}


def pre_process(args):
    # Process training set and use it to decide on the word/character vocabularies
    word_counter, char_counter = Counter(), Counter()
    train_examples, train_eval = process_file(args.train_file, "train", word_counter, char_counter)

    if args.short_test:
        word_emb_mat = load(args.word_emb_file)
        word2idx_dict = load(args.word2idx_file)
        char2idx_dict = load(args.char2idx_file)
        # these are not used in these models (even for the actual BiDAF)
        #char_emb_mat = None
        #char_emb_mat = load(args.char_emb_file, char_emb_mat)
    else:
        word_emb_mat, word2idx_dict = get_embedding(
            word_counter, 'word', emb_file=args.glove_file, vec_size=args.glove_dim, num_vectors=args.glove_num_vecs)
        char_emb_mat, char2idx_dict = get_embedding(char_counter, 'char', emb_file=None, vec_size=args.char_dim)

    # Process dev and test sets
    dev_examples, dev_eval = process_file(args.dev_file, "dev", word_counter, char_counter)
    build_features(args, train_examples, "train", args.train_record_file, word2idx_dict, char2idx_dict)

    # dev_examples used in build_features, which writes the npz file used to eval
    dev_meta = build_features(args, dev_examples, "dev", args.dev_record_file, word2idx_dict, char2idx_dict)

    if args.include_test_examples:
        test_examples, test_eval = process_file(args.test_file, "test", word_counter, char_counter)
        save(args.test_eval_file, test_eval, message="test eval")
        test_meta = build_features(args, test_examples, "test",
                                   args.test_record_file, word2idx_dict, char2idx_dict, is_test=True)
        save(args.test_meta_file, test_meta, message="test meta")


    save(args.word2idx_file, word2idx_dict, message="word dictionary")  # word2idx.json (seems not to be loaded by test)
    save(args.word_emb_file, word_emb_mat, message="word embedding")    # word_emb.json
    save(args.train_eval_file, train_eval, message="train eval")        # train_eval.json
    save(args.dev_eval_file, dev_eval, message="dev eval")              # dev_eval.json

    # new for paraphrase reverse lookup
    idx2word_dict = {value:key for key,value in word2idx_dict.items()}
    save(args.idx2word_file, idx2word_dict, message="NEW idx to word dictionary")
    # these are not used in these models (even for the actual BiDAF)
    #save(args.char_emb_file, char_emb_mat, message="char embedding")    # char_emb.json
    #save(args.char2idx_file, char2idx_dict, message="char dictionary")
    save(args.dev_meta_file, dev_meta, message="dev meta")              # dev_meta.json (not important)


if __name__ == '__main__':
    # Get command-line args
    args_ = get_setup_args()
    if args_.short_test:
        args_.include_test_examples = False
        print(f"short test: {args_.short_test}")

    # Download resources (not needed because already done)
    # download(args_)

    # Import spacy language model
    nlp = spacy.blank("en")

    # Preprocess dataset
    args_.train_file = url_to_data_path(args_.train_url)        # data/train-v2.0.json => train.json
    args_.dev_file = url_to_data_path(args_.dev_url)            # data/dev-v2.0.json => dev_eval.json
    if args_.include_test_examples:
        args_.test_file = url_to_data_path(args_.test_url)

    # for loading glove vectors
    glove_dir = url_to_data_path(args_.glove_url.replace('.zip', ''))
    glove_ext = f'.txt' if glove_dir.endswith('d') else f'.{args_.glove_dim}d.txt'
    args_.glove_file = os.path.join(glove_dir, os.path.basename(glove_dir) + glove_ext)

    # Start the tree paraphraser
    print("Setting up with tree paraphraser model")
    #todo: also set the replacement_node_types
    allen_tree = AllenPredictor(min_phrase_len=args_.allen_tree_max_phrase_len)

    pre_process(args_)
