from google.colab import drive, files
import os
import cv2
import numpy as np
from tqdm import tqdm
from keras.preprocessing.image import img_to_array
import tensorflow as tf
from tensorflow.keras import layers
from tensorflow.keras.applications import VGG19
from tensorflow.keras.models import Model
import matplotlib.pyplot as plt
import re

# Mount Google Drive
drive.mount('/content/drive')

# Function to sort filenames in alphanumeric order
def sorted_alphanumeric(data):
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(data, key=alphanum_key)

# Define the image size and initialize arrays for images
SIZE = 256
clean_images = []
noisy_images = []

# Paths to clean and noisy images
path_clean = '/content/drive/MyDrive/cleared1'
path_noisy = '/content/drive/MyDrive/hazed1'

# Load clean images
if os.path.exists(path_clean):
    files_clean = sorted_alphanumeric(os.listdir(path_clean))
    for i in tqdm(files_clean, desc="Loading clean images"):
        img = cv2.imread(os.path.join(path_clean, i), 1)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (SIZE, SIZE))
        img = img.astype('float32') / 255.0
        clean_images.append(img_to_array(img))
else:
    raise FileNotFoundError(f"Path to clean images not found: {path_clean}")

# Load noisy images
if os.path.exists(path_noisy):
    files_noisy = sorted_alphanumeric(os.listdir(path_noisy))
    for i in tqdm(files_noisy, desc="Loading noisy images"):
        img = cv2.imread(os.path.join(path_noisy, i), 1)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (SIZE, SIZE))
        img = img.astype('float32') / 255.0
        noisy_images.append(img_to_array(img))
else:
    raise FileNotFoundError(f"Path to noisy images not found: {path_noisy}")

# Convert images to TensorFlow datasets
clean_dataset = tf.data.Dataset.from_tensor_slices(np.array(clean_images)).batch(8)
noisy_dataset = tf.data.Dataset.from_tensor_slices(np.array(noisy_images)).batch(8)

# Define downsampling and upsampling functions
def downsample(filters, size, apply_batchnorm=True):
    result = tf.keras.Sequential()
    result.add(layers.Conv2D(filters, size, strides=2, padding='same', use_bias=False))
    if apply_batchnorm:
        result.add(layers.BatchNormalization())
    result.add(layers.LeakyReLU())
    return result

def upsample(filters, size, apply_dropout=False):
    result = tf.keras.Sequential()
    result.add(layers.Conv2DTranspose(filters, size, strides=2, padding='same', use_bias=False))
    result.add(layers.BatchNormalization())
    if apply_dropout:
        result.add(layers.Dropout(0.5))
    result.add(layers.ReLU())
    return result

# Define the Generator model
def Generator():
    inputs = layers.Input(shape=[SIZE, SIZE, 3])
    down_stack = [
        downsample(64, 4, apply_batchnorm=False),
        downsample(128, 4),
        downsample(256, 4),
        downsample(512, 4),
        downsample(512, 4),
        downsample(512, 4),
        downsample(512, 4),
        downsample(512, 4),
    ]
    up_stack = [
        upsample(512, 4, apply_dropout=True),
        upsample(512, 4, apply_dropout=True),
        upsample(512, 4, apply_dropout=True),
        upsample(512, 4),
        upsample(256, 4),
        upsample(128, 4),
        upsample(64, 4),
    ]
    initializer = tf.random_normal_initializer(0., 0.02)
    last = layers.Conv2DTranspose(3, 4, strides=2, padding='same',
                                  kernel_initializer=initializer, activation='tanh')
    x = inputs
    skips = []
    for down in down_stack:
        x = down(x)
        skips.append(x)
    skips = reversed(skips[:-1])
    for up, skip in zip(up_stack, skips):
        x = up(x)
        x = layers.Concatenate()([x, skip])
    x = last(x)
    return tf.keras.Model(inputs=inputs, outputs=x)

# Define the Discriminator model
def Discriminator():
    initializer = tf.random_normal_initializer(0., 0.02)
    inp = layers.Input(shape=[SIZE, SIZE, 3], name='input_image')
    tar = layers.Input(shape=[SIZE, SIZE, 3], name='target_image')
    x = layers.concatenate([inp, tar])
    down1 = downsample(64, 4, False)(x)
    down2 = downsample(128, 4)(down1)
    down3 = downsample(256, 4)(down2)
    zero_pad1 = layers.ZeroPadding2D()(down3)
    conv = layers.Conv2D(512, 4, strides=1, kernel_initializer=initializer, use_bias=False)(zero_pad1)
    batchnorm1 = layers.BatchNormalization()(conv)
    leaky_relu = layers.LeakyReLU()(batchnorm1)
    zero_pad2 = layers.ZeroPadding2D()(leaky_relu)
    last = layers.Conv2D(1, 4, strides=1, kernel_initializer=initializer)(zero_pad2)
    return tf.keras.Model(inputs=[inp, tar], outputs=last)

# Initialize models and optimizers
generator = Generator()
discriminator = Discriminator()
generator_optimizer = tf.keras.optimizers.Adam(2e-4, beta_1=0.5)
discriminator_optimizer = tf.keras.optimizers.Adam(2e-4, beta_1=0.5)

# Define loss functions
loss_object = tf.keras.losses.BinaryCrossentropy(from_logits=True)
LAMBDA = 100

def generator_loss(disc_generated_output, gen_output, target):
    gan_loss = loss_object(tf.ones_like(disc_generated_output), disc_generated_output)
    l1_loss = tf.reduce_mean(tf.abs(target - gen_output))
    return gan_loss + (LAMBDA * l1_loss), gan_loss, l1_loss

def discriminator_loss(disc_real_output, disc_generated_output):
    real_loss = loss_object(tf.ones_like(disc_real_output), disc_real_output)
    generated_loss = loss_object(tf.zeros_like(disc_generated_output), disc_generated_output)
    return real_loss + generated_loss

# Training step
@tf.function
def train_step(input_image, target):
    with tf.GradientTape() as gen_tape, tf.GradientTape() as disc_tape:
        gen_output = generator(input_image, training=True)
        disc_real_output = discriminator([input_image, target], training=True)
        disc_generated_output = discriminator([input_image, gen_output], training=True)

        gen_total_loss, gen_gan_loss, gen_l1_loss = generator_loss(disc_generated_output, gen_output, target)
        disc_loss = discriminator_loss(disc_real_output, disc_generated_output)

    generator_gradients = gen_tape.gradient(gen_total_loss, generator.trainable_variables)
    discriminator_gradients = disc_tape.gradient(disc_loss, discriminator.trainable_variables)

    generator_optimizer.apply_gradients(zip(generator_gradients, generator.trainable_variables))
    discriminator_optimizer.apply_gradients(zip(discriminator_gradients, discriminator.trainable_variables))

    return gen_gan_loss, gen_l1_loss, disc_loss

# Training loop
def fit(dataset, epochs):
    for epoch in range(epochs):
        print(f"Epoch {epoch + 1}/{epochs}")
        for input_image, target in dataset:
            train_step(input_image, target)

# Combine datasets for training
train_dataset = tf.data.Dataset.zip((noisy_dataset, clean_dataset))

# Start training
fit(train_dataset, epochs=250)

# Function to upload and test multiple images
def upload_and_test_images():
    print("Testing phase: Upload noisy images one by one to see the dehazed results.")
    while True:
        uploaded = files.upload()
        for filename in uploaded.keys():
            try:
                img = cv2.imread(filename)
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                img = cv2.resize(img, (SIZE, SIZE))
                img = img.astype('float32') / 255.0
                img = np.expand_dims(img, axis=0)

                prediction = generator(img, training=False)

                img_clipped = np.clip(img[0], 0, 1)
                prediction_clipped = np.clip(prediction[0], 0, 1)

                plt.figure(figsize=(10, 5))
                plt.subplot(1, 2, 1)
                plt.title("Noisy Image")
                plt.imshow(img_clipped)
                plt.axis("off")

                plt.subplot(1, 2, 2)
                plt.title("Dehazed Image")
                plt.imshow(prediction_clipped)
                plt.axis("off")
                plt.show()
            except Exception as e:
                print(f"Error processing file {filename}: {e}")
        print("Upload another image or stop execution to end testing.")

# Start the testing phase after training
print("Training completed. Starting testing phase.")
upload_and_test_images()
