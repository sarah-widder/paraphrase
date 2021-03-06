''' This module contains the encoder, decoder, supertag encoder, training, and evaluation code for paraphrase generation.
    Modify the model parameters and save paths, then `python model.py` to train the model.'''
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

from attention import DotproductAttention

# following code adapted from: 
# https://pytorch.org/tutorials/intermediate/seq2seq_translation_tutorial.html

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

SOS_token = 0
EOS_token = 1

SOS_token = 0
EOS_token = 1

MAX_LENGTH = 15
MIN_LENGTH = 7

# UPDATE THESE PARAMETERS FOR EACH MODEL RUN
HIDDEN_SIZE = 100
BIDIR_SUPERTAGS = True
TRAIN_DIR = 'data/train' # location of training data
TEST_DIR = 'data/test' # location of test data
SAVE_DIR = 'model/model-checkpoints/new-data' # where to save model checkpoints
NUM_ITERATIONS = 500000
REORDERED = False

class Lang:
    ''' Holds textual data with index dictionaries '''
    def __init__(self, name):
        self.name = name
        self.word2index = {}
        self.word2count = {}
        self.index2word = {0: "SOS", 1: "EOS"}
        self.n_words = 2  # Count SOS and EOS

    def addSentence(self, sentence):
        for word in sentence.split(' '):
            self.addWord(word)

    def addWord(self, word):
        if word not in self.word2index:
            self.word2index[word] = self.n_words
            self.word2count[word] = 1
            self.index2word[self.n_words] = word
            self.n_words += 1
        else:
            self.word2count[word] += 1

def filterPair(p):
    ''' Returns True if all three sequences in p are shorter than MAX_LENGTH '''
    return len(p[0].split(' ')) < MAX_LENGTH and \
        len(p[1].split(' ')) < MAX_LENGTH and \
            len(p[2].split(' ')) < MAX_LENGTH # and \
                # len(p[0].split(' ')) > MIN_LENGTH and \
                #     len(p[1].split(' ')) > MIN_LENGTH and \
                #         len(p[2].split(' ')) > MIN_LENGTH


def filterPairs(pairs):
    ''' Removes pairs that do not meet conditions in filterPair '''
    return [pair for pair in pairs if filterPair(pair)]

# Turn a Unicode string to plain ASCII, thanks to
# https://stackoverflow.com/a/518232/2809427
def unicodeToAscii(s):
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )

# Lowercase, trim, and remove non-letter characters
def normalizeString(s):
    s = unicodeToAscii(s.lower().strip())
    s = re.sub(r"([.!?])", r" \1", s)
    s = re.sub(r"[^a-zA-Z.!?]+", r" ", s)
    return s

def readLangs(lang1, lang2, test=False, reverse=False, openNMT=False):
    ''' Reads in data for training or testing. Be sure to update filenames in open statements as needed.'''
    print("Reading lines...")
    data_dir = TRAIN_DIR
    prefix = 'train'
    if test:
        data_dir = TEST_DIR
        prefix = 'test'

    if not REORDERED:
        # Read the file and split into lines
        ref_lines = open('{}/{}-ref-words.txt'.format(data_dir, prefix), encoding='utf-8').\
            read().strip().split('\n')
        
        para_lines = open('{}/{}-para-words.txt'.format(data_dir, prefix), encoding='utf-8').\
            read().strip().split('\n')

        if openNMT:
            tags = open('{}/{}-opennmt-supertags.txt'.format(data_dir, prefix), encoding='utf-8').read().strip().split('\n')
        else:
            tags = open('{}/{}-para-supertags.txt'.format(data_dir, prefix), encoding='utf-8').read().strip().split('\n')
    else:
        # Read the file and split into lines
        ref_lines = open('{}/{}-ref-ordered-words.txt'.format(data_dir, prefix), encoding='utf-8').\
            read().strip().split('\n')
        
        para_lines = open('{}/{}-para-reordered-words.txt'.format(data_dir, prefix), encoding='utf-8').\
            read().strip().split('\n')

        tags = open('{}/{}-para-reordered-supertags.txt'.format(data_dir, prefix), encoding='utf-8').read().strip().split('\n')
   
    # Split every line into pairs and normalize
    pairs = list(zip([normalizeString(l) for l in ref_lines], [normalizeString(l) for l in para_lines], tags))

    # Reverse pairs, make Lang instances
    if reverse:
        pairs = [list(reversed(p)) for p in pairs]
        input_lang = Lang(lang2)
        output_lang = Lang(lang1)
    else:
        input_lang = Lang(lang1)
        output_lang = Lang(lang2)

    supertag_lang = Lang('supertags')

    return input_lang, output_lang, supertag_lang, pairs

def prepareData(lang1, lang2, test=False, reverse=False, openNMT=False):
    ''' Reads, filters, and counts data for training and test '''
    input_lang, output_lang, supertag_lang, pairs = readLangs(lang1, lang2, test, reverse, openNMT)
    print("Read %s sentence pairs" % len(pairs))
    pairs = filterPairs(pairs)
    print("Trimmed to %s sentence pairs" % len(pairs))
    print("Counting words...")
    for pair in pairs:
        input_lang.addSentence(pair[0])
        output_lang.addSentence(pair[1])
        supertag_lang.addSentence(pair[2])
    print("Counted words:")
    print(input_lang.name, input_lang.n_words)
    print(output_lang.name, output_lang.n_words)
    print(supertag_lang.name, supertag_lang.n_words)
    return input_lang, output_lang, supertag_lang, pairs



class EncoderRNN(nn.Module):
    ''' Basic encoder for the input sentence '''
    def __init__(self, input_size, hidden_size):
        super(EncoderRNN, self).__init__()
        self.hidden_size = hidden_size

        self.embedding = nn.Embedding(input_size, hidden_size)
        self.lstm = nn.LSTM(hidden_size, hidden_size)

    def forward(self, input, hidden, c):
        embedded = self.embedding(input).view(1, 1, -1)
        output = embedded
        output, (hidden, c) = self.lstm(output, (hidden,c))
        return output, (hidden, c)

    def initHidden(self):
        return (torch.zeros(1, 1, self.hidden_size, device=device),torch.zeros(1, 1, self.hidden_size, device=device))

# modified BiLSTM from:
# https://github.com/yunjey/pytorch-tutorial/blob/master/tutorials/02-intermediate/bidirectional_recurrent_neural_network/main.py
class BiLSTM(nn.Module):
    ''' Bidirectional encoder for supertag sequences '''
    def __init__(self, input_size, hidden_size):
        super(BiLSTM, self).__init__()
        self.hidden_size = hidden_size
        
        self.embedding = nn.Embedding(input_size, hidden_size)
        self.lstm = nn.LSTM(hidden_size, hidden_size, batch_first=True, bidirectional=True)
    
    def forward(self, input, h0, c0):
        
        # embed
        embedded = self.embedding(input).view(1,1,-1)
        output = embedded
        # Forward propagate LSTM
        output, (h0, c0) = self.lstm(embedded, (h0, c0))
        
        return output, (h0,c0)

    def initHidden(self):
        # print('init hidden with dim', torch.zeros(2, 1, self.hidden_size, device=device).shape)
        return torch.zeros(2, 1, self.hidden_size, device=device), torch.zeros(2, 1, self.hidden_size, device=device)

    

class AttnDecoderRNN(nn.Module):
    ''' Decoder that takes in hidden states from the encoder and supertag encoder and uses attention over encoder states'''
    def __init__(self, attn, hidden_size, output_size, dropout_p=0.1, max_length=MAX_LENGTH, bidir_supertags=BIDIR_SUPERTAGS):
        super(AttnDecoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.dropout_p = dropout_p
        self.max_length = max_length

        self.embedding = nn.Embedding(self.output_size, self.hidden_size)
        self.attn = attn
        if bidir_supertags:
            self.hidden_combine = nn.Linear(self.hidden_size * 4, self.hidden_size)
            self.attn_combine = nn.Linear(self.hidden_size * 4, self.hidden_size)
        else:
            self.hidden_combine = nn.Linear(self.hidden_size * 3, self.hidden_size)
            self.attn_combine = nn.Linear(self.hidden_size * 3, self.hidden_size)
        self.dropout = nn.Dropout(self.dropout_p)
        self.lstm = nn.LSTM(self.hidden_size, self.hidden_size)
        self.out = nn.Linear(self.hidden_size, self.output_size)

    def forward(self, inpt, hidden, c, encoder_outputs, supertag_hidden):
        embedded = self.embedding(inpt).view(1, 1, -1)
        embedded = self.dropout(embedded)

        # concat supertag hidden for current tag with embedded input
        inp = torch.cat((embedded[0], supertag_hidden.view(1,-1)), 1)
        inp_hidden = self.hidden_combine(torch.cat((inp, hidden[0]), 1))

        # positional attention
        # attn_weights = F.softmax(
            # self.attn(torch.cat((inp, hidden[0]), 1)), dim=1)


        # encoder_attn = self.attn2(encoder_outputs)
        # dot(attn_weights, encoder_attn)
        # multiply this by encoder outputs to get attn

        # use DotProductAttention module
        attn_weights = self.attn(encoder_outputs, inp_hidden)

        attn_applied = torch.bmm(attn_weights.unsqueeze(0),
                                 encoder_outputs.unsqueeze(0))

        output = torch.cat((inp, attn_applied[0]), 1)
        output = self.attn_combine(output).unsqueeze(0)

        output = F.relu(output)
        # output = torch.cat((output, hidden[:,:,self.hidden_size:]),2)
        output, (hidden, c) = self.lstm(output, (hidden, c))

        output = F.log_softmax(self.out(output[0]), dim=1)
        return output, hidden, c, attn_weights

    def initHidden(self):
        return torch.zeros(1, 1, self.hidden_size, device=device), torch.zeros(1, 1, self.hidden_size, device=device)


def indexesFromSentence(lang, sentence):
    ''' Translates a sentence into a sequence of indices corresponding to the vocab'''
    return [lang.word2index[word] for word in sentence.split(' ') if word in lang.word2index]


def tensorFromSentence(lang, sentence):
    ''' Creates a PyTorch Tensor of indices from a sentence '''
    indexes = indexesFromSentence(lang, sentence)
    indexes.append(EOS_token)
    return torch.tensor(indexes, dtype=torch.long, device=device).view(-1, 1)


def tensorsFromPair(pair):
    ''' Creates a Pytorch Tensor for the input sentence, output sentence, and supertag sequence '''
    input_tensor = tensorFromSentence(input_lang, pair[0])
    target_tensor = tensorFromSentence(output_lang, pair[1])
    supertag_tensor = tensorFromSentence(supertag_lang, pair[2])
    return (input_tensor, target_tensor, supertag_tensor)


def train(input_tensor, supertag_tensor, target_tensor, 
            encoder, supertag_encoder, decoder, 
            encoder_optimizer, supertag_encoder_optimizer, decoder_optimizer, 
            criterion, max_length=MAX_LENGTH, bidir_supertags=BIDIR_SUPERTAGS):
    ''' Performs one training step using the given input_tensor, supertag_tensor, and target_tensor '''
    encoder_hidden, encoder_c = encoder.initHidden()
    if bidir_supertags:
        supertag_hidden, c0 = supertag_encoder.initHidden()
    else:
        supertag_hidden = supertag_encoder.initHidden()

    encoder_optimizer.zero_grad()
    supertag_encoder_optimizer.zero_grad()
    decoder_optimizer.zero_grad()

    input_length = input_tensor.size(0)
    target_length = target_tensor.size(0)
    supertag_length = supertag_tensor.size(0)

    encoder_outputs = torch.zeros(max_length, encoder.hidden_size, device=device)
    if bidir_supertags:
        supertag_enc_hiddens = torch.zeros(max_length, 2*supertag_encoder.hidden_size, device=device)
    else:
        supertag_enc_hiddens = torch.zeros(max_length, supertag_encoder.hidden_size, device=device)

    loss = 0

    for ei in range(input_length):
        encoder_output, (encoder_hidden, encoder_c) = encoder(
            input_tensor[ei], encoder_hidden, encoder_c)
        encoder_outputs[ei] = encoder_output[0, 0]

    for ei in range(supertag_length):
        if bidir_supertags:
            supertag_output, (supertag_hidden,c0) = supertag_encoder(supertag_tensor[ei], supertag_hidden,c0)
            supertag_enc_hiddens[ei] = supertag_hidden.view(1,1,-1)
        else:
            supertag_output, supertag_hidden = supertag_encoder(supertag_tensor[ei], supertag_hidden)
            supertag_enc_hiddens[ei] = supertag_output[0, 0]


    # supertag_output = torch.cat((supertag_enc_outputs[0][supertag_encoder.hidden_size:], supertag_enc_outputs[-1][:supertag_encoder.hidden_size]))

    decoder_input = torch.tensor([[SOS_token]], device=device)

    decoder_hidden = encoder_hidden
    decoder_c = encoder_c
    # decoder_hidden = torch.cat((encoder_hidden, supertag_hidden.view(1,1,-1)), dim=2).view(1,1,-1)
    
    use_teacher_forcing = True if random.random() < teacher_forcing_ratio else False

    if use_teacher_forcing:
        # Teacher forcing: Feed the target as the next input
        for di in range(target_length):
            decoder_output, decoder_hidden, decoder_c, decoder_attention = decoder(
                decoder_input, decoder_hidden, decoder_c, encoder_outputs, supertag_enc_hiddens[di])
            loss += criterion(decoder_output, target_tensor[di])
            decoder_input = target_tensor[di]  # Teacher forcing

    else:
        # Without teacher forcing: use its own predictions as the next input
        for di in range(target_length):
            decoder_output, decoder_hidden, decoder_c, decoder_attention = decoder(
                decoder_input, decoder_hidden, decoder_c, encoder_outputs, supertag_enc_hiddens[di])
            topv, topi = decoder_output.topk(1)
            decoder_input = topi.squeeze().detach()  # detach from history as input

            loss += criterion(decoder_output, target_tensor[di])
            if decoder_input.item() == EOS_token:
                break

    loss.backward()

    encoder_optimizer.step()
    decoder_optimizer.step()

    return loss.item() / target_length

def asMinutes(s):
    m = math.floor(s / 60)
    s -= m * 60
    return '%dm %ds' % (m, s)


def timeSince(since, percent):
    now = time.time()
    s = now - since
    es = s / (percent)
    rs = es - s
    return '%s (- %s)' % (asMinutes(s), asMinutes(rs))

def trainIters(encoder, supertag_encoder, decoder, n_iters, print_every=1000, plot_every=100, learning_rate=0.01, bidir_supertags=True):
    ''' Performs n_iters training steps, periodically saving model checkpoints '''
    start = time.time()
    plot_losses = []
    print_loss_total = 0  # Reset every print_every
    plot_loss_total = 0  # Reset every plot_every

    encoder_optimizer = optim.SGD(encoder.parameters(), lr=learning_rate)
    decoder_optimizer = optim.SGD(decoder.parameters(), lr=learning_rate)
    supertag_encoder_optimizer = optim.SGD(supertag_encoder.parameters(), lr=learning_rate)

    training_pairs = [tensorsFromPair(random.choice(pairs))
                      for i in range(n_iters)]
    criterion = nn.NLLLoss()

    for iter in range(1, n_iters + 1):
        training_pair = training_pairs[iter - 1]
        input_tensor = training_pair[0]
        target_tensor = training_pair[1]
        supertag_tensor = training_pair[2]

        loss = train(input_tensor, supertag_tensor, target_tensor, encoder, supertag_encoder,
                     decoder, encoder_optimizer, supertag_encoder_optimizer, decoder_optimizer, criterion, bidir_supertags=bidir_supertags)
        print_loss_total += loss
        plot_loss_total += loss

        if iter % print_every == 0:
            print_loss_avg = print_loss_total / print_every
            print_loss_total = 0
            torch.save(encoder.state_dict(), SAVE_DIR + 'encoder_step_{}.pt'.format(iter))
            torch.save(supertag_encoder.state_dict(), SAVE_DIR + 'supertag_encoder_step_{}.pt'.format(iter))
            torch.save(decoder.state_dict(), SAVE_DIR + 'decoder_step_{}.pt'.format(iter))
            torch.save(attn.state_dict(), SAVE_DIR + 'attn_step_{}.pt'.format(iter))

            print('%s (%d %d%%) %.4f' % (timeSince(start, iter / n_iters),
                                         iter, iter / n_iters * 100, print_loss_avg))

        if iter % plot_every == 0:
            plot_loss_avg = plot_loss_total / plot_every
            plot_losses.append(plot_loss_avg)
            plot_loss_total = 0

    showPlot(plot_losses)

def showPlot(points):
    plt.figure()
    fig, ax = plt.subplots()
    # this locator puts ticks at regular intervals
    loc = ticker.MultipleLocator(base=0.2)
    ax.yaxis.set_major_locator(loc)
    plt.plot(points)

def evaluate(encoder, supertag_encoder, decoder, sentence, supertags, input_lang, supertag_lang, output_lang, max_length=MAX_LENGTH, bidir_supertags=True):
    ''' Uses the trained model to generate a paraphrase for a new sentence and supertag sequence '''
    with torch.no_grad():
        input_tensor = tensorFromSentence(input_lang, sentence)
        supertag_tensor = tensorFromSentence(supertag_lang, supertags)

        input_length = input_tensor.size()[0]
        supertag_length = supertag_tensor.size()[0]

        encoder_hidden, encoder_c = encoder.initHidden()
        if bidir_supertags:
            supertag_hidden, c0 = supertag_encoder.initHidden()
        else: 
            supertag_hidden = supertag_encoder.initHidden()


        encoder_outputs = torch.zeros(max_length, encoder.hidden_size, device=device)
        if bidir_supertags:
            supertag_enc_hiddens = torch.zeros(max_length, 2*supertag_encoder.hidden_size, device=device)
        else:
            supertag_enc_hiddens = torch.zeros(max_length, supertag_encoder.hidden_size, device=device)
        # supertag_enc_outputs = torch.zeros(max_length, 2*supertag_encoder.hidden_size, device=device)

        for ei in range(input_length):
            encoder_output, (encoder_hidden, encoder_c) = encoder(input_tensor[ei],
                                                     encoder_hidden, encoder_c)
            encoder_outputs[ei] += encoder_output[0, 0]

        for ei in range(supertag_length):

            if bidir_supertags:
                supertag_output, (supertag_hidden,c0) = supertag_encoder(supertag_tensor[ei], supertag_hidden,c0)
                supertag_enc_hiddens[ei] = supertag_hidden.view(1,1,-1)
            else:
                supertag_output, supertag_hidden = supertag_encoder(supertag_tensor[ei], supertag_hidden)
                supertag_enc_hiddens[ei] = supertag_output[0, 0]


        decoder_input = torch.tensor([[SOS_token]], device=device)  # SOS

        decoder_hidden = encoder_hidden
        decoder_c = encoder_c
        # decoder_hidden = torch.cat((encoder_hidden, supertag_hidden.view(1,1,-1)), dim=2).view(1,1,-1)


        decoded_words = []
        decoder_attentions = torch.zeros(max_length, max_length)

        for di in range(max_length):
            decoder_output, decoder_hidden, decoder_c, decoder_attention = decoder(
                decoder_input, decoder_hidden, decoder_c, encoder_outputs, supertag_enc_hiddens[di])
            decoder_attentions[di] = decoder_attention.data
            topv, topi = decoder_output.data.topk(1)
            if topi.item() == EOS_token:
                decoded_words.append('<EOS>')
                break
            else:
                decoded_words.append(output_lang.index2word[topi.item()])

            decoder_input = topi.squeeze().detach()

        return decoded_words, decoder_attentions[:di + 1]

def evaluateRandomly(encoder, supertag_encoder, decoder, input_lang, supertag_lang, output_lang, n=10, bidir_supertags=True):
    ''' Randomly generates 10 paraphrases for 10 sentence and supertag sequences using the trained model'''
    for i in range(n):
        pair = random.choice(pairs)
        print('>', pair[0])
        print('=', pair[1])
        output_words, attentions = evaluate(encoder, supertag_encoder, decoder, pair[0], pair[2], input_lang, supertag_lang, output_lang, bidir_supertags=bidir_supertags)
        output_sentence = ' '.join(output_words)
        print('<', output_sentence)
        print('')


if __name__ == '__main__':
    input_lang, output_lang, supertag_lang, pairs = prepareData('ref', 'para')
    print(random.choice(pairs))

    teacher_forcing_ratio = 0.5

    hidden_size = HIDDEN_SIZE
    encoder1 = EncoderRNN(input_lang.n_words, hidden_size).to(device)

    if BIDIR_SUPERTAGS:
        supertag_encoder1 = BiLSTM(supertag_lang.n_words, hidden_size).to(device)
    else:
        supertag_encoder1 = EncoderRNN(supertag_lang.n_words, hidden_size).to(device)

    attn = DotproductAttention().to(device)

    attn_decoder1 = AttnDecoderRNN(attn, hidden_size, output_lang.n_words, dropout_p=0.1, bidir_supertags=BIDIR_SUPERTAGS).to(device)

    trainIters(encoder1, supertag_encoder1, attn_decoder1, NUM_ITERATIONS, print_every=10000, bidir_supertags=BIDIR_SUPERTAGS)
    evaluateRandomly(encoder1, supertag_encoder1, attn_decoder1, input_lang, supertag_lang, output_lang)
