''' Loads saved models from checkpoints and runs evaluation on a full test set.'''
from io import open
import unicodedata
import string
import re
import random
import torch
import torch.nn as nn
import time
import math
import matplotlib.pyplot as plt
plt.switch_backend('agg')
import matplotlib.ticker as ticker
import numpy as np

from torch import optim
import torch.nn.functional as F

from model import EncoderRNN, BiLSTM, AttnDecoderRNN, prepareData, evaluate
from attention import DotproductAttention

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

HIDDEN_SIZE = 100
BIDIR_SUPERTAGS = True
SAVE_DIR = 'model/model-checkpoints/new-data'
OUTPUT_DIR = 'data/'
NUM_ITERATIONS = 500000
REORDERED = False

if __name__ == '__main__':
    input_lang, output_lang, supertag_lang, pairs = prepareData('ref', 'para', test=False)
    teacher_forcing_ratio = 0.5
    hidden_size = HIDDEN_SIZE


    encoder1 = EncoderRNN(input_lang.n_words, hidden_size).to(device)
    encoder1.load_state_dict(torch.load(SAVE_DIR + 'encoder_step_{}.pt'.format(NUM_ITERATIONS)))
    encoder1.eval()

    if BIDIR_SUPERTAGS:
        supertag_encoder1 = BiLSTM(supertag_lang.n_words, hidden_size).to(device)
    else:
        supertag_encoder1 = EncoderRNN(supertag_lang.n_words, hidden_size).to(device)

    supertag_encoder1.load_state_dict(torch.load(SAVE_DIR + 'supertag_encoder_step_{}.pt'.format(NUM_ITERATIONS)))
    supertag_encoder1.eval()

    attn = DotproductAttention().to(device)
    attn.load_state_dict(torch.load(SAVE_DIR + 'attn_step_{}.pt'.format(NUM_ITERATIONS)))
    attn.eval()

    attn_decoder1 = AttnDecoderRNN(attn, hidden_size, output_lang.n_words, dropout_p=0.1,bidir_supertags=BIDIR_SUPERTAGS).to(device)
    attn_decoder1.load_state_dict(torch.load(SAVE_DIR + 'decoder_step_{}.pt'.format(NUM_ITERATIONS)))
    attn_decoder1.eval()

    test_input, test_output, test_supertags, test_pairs = prepareData('test-ref', 'test-para', test=True, openNMT=False)

    dr = 'bidir' if BIDIR_SUPERTAGS else 'uni'
    order = 'hier' if REORDERED else 'lin'
    save_path = 'test-' + dr + '-' + order + '-' + str(HIDDEN_SIZE) + '-output.txt'

    with open(OUTPUT_DIR + save_path, 'w') as f:
        for pair in test_pairs:
            output_words, attentions = evaluate(encoder1, supertag_encoder1, attn_decoder1, pair[0], pair[2], input_lang, supertag_lang, output_lang, bidir_supertags=BIDIR_SUPERTAGS)
            output_sentence = ' '.join(output_words)
            f.write(output_sentence + '\n')
