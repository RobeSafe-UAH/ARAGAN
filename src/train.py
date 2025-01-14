import os
# Do not show all the mesagges genrated by tensorflow
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2' 
import tensorflow as tf

import os
import pathlib
import time
import datetime

from matplotlib import pyplot as plt
# from IPython import display
from tqdm import tqdm
import numpy as np

from dataloader_pipeline import Dataloader
from models import Models
from typing import Tuple, Type


class ARAGAN(object):
    def __init__(self):
        # Buffer size, complete training set length 
        self.BUFFER_SIZE = 98723
        
        self.BATCH_SIZE = 8
        # Each image is 256x256 in size
        self.IMG_WIDTH = 256
        self.IMG_HEIGHT = 256

        #  GENERATOR
        self.OUTPUT_CHANNELS = 3
        # Lamda parameter to calculate the loss function, this parameter will 
        # set the weight of the L1 loss
        self.LAMBDA = 100

        # Training epochs
        self.EPOCHS = 100
        # Number of images in both sets
        self.TOTAL_IMGS = 98723
        self.TOTAL_IMGS_TEST = 32196

        # Cublass error
        gpus = tf.config.experimental.list_physical_devices('GPU')
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)

        # Call the Models class setting some parameters 
        self.models = Models(self.IMG_WIDTH, 
                             self.IMG_HEIGHT,
                             self.OUTPUT_CHANNELS,
                             self.BATCH_SIZE)
        
        # Search the models available to be choose by the developer 
        method_list = [method for method in dir(Models) 
                       if method.startswith('__') is False]
        print('\033[1;32m Models available: \033[0;0m', method_list)
        # Choose the Generator architecture from the list
        self.name = input('Choose the Generator from the list above: ')
        
        # Call the Generator and the Discriminator
        self.generator = eval('self.models.' + self.name + '()')
        self.discriminator = self.models.Discriminator()
        self.discriminator.summary()
        
        # Create the BCE loss
        self.loss_object = tf.keras.losses.BinaryCrossentropy(from_logits=True)
        # Create the KLD metric
        self.loss_kld = tf.keras.losses.KLDivergence()
        
        # Create the schelue for the learning rate
        self.learning_rate = tf.keras.optimizers.schedules.ExponentialDecay(
            initial_learning_rate=1e-2,
            decay_steps=10000,
            decay_rate=0.9)
        
        # Create the optimizer for the generator and the discriminator 
        self.generator_optimizer = tf.keras.optimizers.Adam(
            self.learning_rate,
            beta_1=0.5)
        self.discriminator_optimizer = tf.keras.optimizers.Adam(
            self.learning_rate, 
            beta_1=0.5)
        
        # Set logs folder, tensorboard writer
        self.log_dir="logs/"
        self.summary_writer = tf.summary.create_file_writer(
            self.log_dir + "fit/" + 
            datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + 
            "_" + self.name + "_" + str(self.BATCH_SIZE))
        
        # Set checkpoints folder 
        self.checkpoint_dir = './training_checkpoints'
        self.chekpoint_name =  self.name + "_"+ str(self.BATCH_SIZE) + "/epoch_"
        self.checkpoint_prefix = os.path.join(self.checkpoint_dir, 
                                              self.chekpoint_name)
        
    def generator_loss(self, 
                       disc_generated_output: tf.Tensor,
                       gen_output: tf.Tensor, 
                       target: tf.Tensor) -> Tuple[tf.Tensor, 
                                                   tf.Tensor, 
                                                   tf.Tensor]:
        ''' Generator loss 
        BCE -> Binary CrossEntropy
        BCE(ones, discriminator output) + lamda * L1_loss(GT, generator output)

        Args:
            disc_generated_output (tf.Tensor): output generated by the 
            discriminator
            gen_output (tf.Tensor): output generated by the generator 
            target (tf.Tensor): ground truth from the dataset 

        Returns:
            Tuple[tf.Tensor, tf.Tensor, tf.Tensor]: total_gen_loss, gan_loss, 
            l1_loss
        '''
        gan_loss = self.loss_object(tf.ones_like(disc_generated_output), 
                                    disc_generated_output)

        # Mean absolute error
        l1_loss = tf.reduce_mean(tf.abs(target - gen_output))

        total_gen_loss = gan_loss + (self.LAMBDA * l1_loss)

        return total_gen_loss, gan_loss, l1_loss

    def discriminator_loss(self,
                           disc_real_output: tf.Tensor, 
                           disc_generated_output: tf.Tensor) -> tf.Tensor:
        '''Discriminator loss
        BCE(ones, )

        Args:
            disc_real_output (tf.Tensor): discriminator output of the GT image
            disc_generated_output (tf.Tensor): discriminator output of the 
            generated image

        Returns:
            tf.Tensor: discriminator loss
        '''
        real_loss = self.loss_object(tf.ones_like(disc_real_output),
                                     disc_real_output)

        generated_loss = self.loss_object(tf.zeros_like(disc_generated_output),
                                          disc_generated_output)

        total_disc_loss = real_loss + generated_loss

        return total_disc_loss


    def calculate_metrics(self,
                          target: tf.Tensor, 
                          gen_output: tf.Tensor) -> Tuple[tf.Tensor, 
                                                          tf.Tensor, 
                                                          tf.Tensor]:
        '''Function to calculate model metrics:
        Kullback-Leibler Divergence (KLD)
        Pearson’s Correlation Coefficient, CC
        Shuffled Area Under the ROC Curve (s-AUC)
        
        Args:
            target (tf.Tensor): attention map from the GT
            gen_output (tf.Tensor): attention map generated by the generator

        Returns:
            Tuple[tf.Tensor, tf.Tensor, tf.Tensor]: output metrics
        '''

        
        kld_metric =  self.loss_kld(target, gen_output)
        mae_metric =  tf.keras.metrics.mean_absolute_error(target, gen_output)
        mse_metric =  tf.keras.metrics.mean_squared_error(target, gen_output)

        kld_metric = tf.reduce_mean(kld_metric)
        mae_metric = tf.reduce_mean(mae_metric)
        mse_metric = tf.reduce_mean(mse_metric)

        return  kld_metric, mae_metric, mse_metric

    @tf.function
    def train_step(self,
                   input_image: tf.Tensor,
                   target: tf.Tensor,
                   step: int,
                   epoch: int) -> Tuple[tf.Tensor, tf.Tensor, tf.Tensor]:
        '''Train step for every batch of the training dataset

        Args:
            input_image (tf.Tensor): RGB image
            target (tf.Tensor): Attention map from th GT
            step (int): train step
            epoch (int): Train epoch

        Returns:
            Tuple[tf.Tensor, tf.Tensor, tf.Tensor]: losses to be evaluated later
        '''
        # Create variables for memory optimization
        disc_real_output = None
        disc_gen_output = None
        gen_total_loss = None
        gen_gan_loss = None
        gen_l1_loss = None
        disc_loss = None

        with tf.GradientTape() as gen_tape, tf.GradientTape() as disc_tape:
        # Forward pass
            gen_output = self.generator(input_image, training=True)
            disc_real_output = self.discriminator([input_image, target],
                                                  training=True)
            disc_gen_output = self.discriminator([input_image, gen_output],
                                                 training=True)

            # Generator Loss value for this batch
            gen_total_loss, gen_gan_loss, gen_l1_loss = self.generator_loss(
                disc_gen_output, 
                gen_output, 
                target)
            # Discriminator Loss value for this batch
            disc_loss = self.discriminator_loss(disc_real_output, 
                                                disc_gen_output)

        # Get generator gradients of loss wrt the weights
        generator_gradients = gen_tape.gradient(
            gen_total_loss,
            self.generator.trainable_variables)
        # Get discriminator gradients of loss wrt the weights
        discriminator_gradients = disc_tape.gradient(
            disc_loss,
            self.discriminator.trainable_variables)

        # Update the weights of the generator
        self.generator_optimizer.apply_gradients(
            zip(generator_gradients, 
                self.generator.trainable_variables))
        # Update the weights of the discriminator
        self.discriminator_optimizer.apply_gradients(
            zip(discriminator_gradients,
                self.discriminator.trainable_variables))

        # Calculate metrics for this batch
        kld_metric, mae_metric, mse_metric = self.calculate_metrics(target, 
                                                                    gen_output)

        # Write logs for Tensorboard 
        tensorboard_step = step * self.BATCH_SIZE + epoch * self.TOTAL_IMGS
        with self.summary_writer.as_default():
            tf.summary.scalar('gen_total_loss', 
                              gen_total_loss, 
                              step = tensorboard_step)
            tf.summary.scalar('gen_gan_loss',
                              gen_gan_loss, 
                              step = tensorboard_step)
            tf.summary.scalar('gen_l1_loss', 
                              gen_l1_loss,
                              step = tensorboard_step)
            tf.summary.scalar('disc_loss',
                              disc_loss,
                              step = tensorboard_step)
            tf.summary.scalar('kld_metric',
                              kld_metric, 
                              step = tensorboard_step)
            tf.summary.scalar('mae_metric', 
                              mae_metric,
                              step = tensorboard_step)
            tf.summary.scalar('mse_metric',
                              mse_metric,
                              step = tensorboard_step)

        return gen_total_loss, gen_gan_loss, disc_loss

    def fit(self, train_ds: tf.data.Dataset, test_ds: tf.data.Dataset) -> None:
        '''Train function to pass the batchs from the dataset to the train step
        procedure

        Args:
            train_ds (tf.data.Dataset): Training dataset
            test_ds (tf.data.Dataset): Testing dataset
        '''
        # Itreate over epochs 
        for epoch in range(self.EPOCHS):
            gen_total_losses = []
            gen_gan_losses = []
            disc_losses = []
            # Iterate the training dataset in batches 
            for step, (input_image, target) in tqdm(train_ds.enumerate()):
                gen_total_loss, gen_gan_loss, disc_loss = self.train_step(
                    input_image, target, step, epoch)
                # Store losses 
                gen_total_losses.append(gen_total_loss)
                gen_gan_losses.append(gen_gan_loss)
                disc_losses.append(disc_loss)

            self.test(test_ds, epoch)
            # Save generator for inference
            self.checkpoint_prefix = os.path.join(
                self.checkpoint_dir, 
                self.chekpoint_name + str(epoch))
            self.generator.save(self.checkpoint_prefix)
            print ('Saving checkpoint for epoch {}'.format(epoch+1))            
            print("gen_total_loss {:1.2f}".format(np.mean(gen_total_losses)))
            print("gen_gan_loss {:1.2f}".format(np.mean(gen_gan_losses)))  
            print("disc_loss {:1.2f}".format(np.mean(disc_losses)))                                                
                                                        
    @tf.function
    def test_step(self, 
                  input_image: tf.Tensor, 
                  target: tf.Tensor, 
                  step: int,
                  epoch: int) -> None:
        '''Test step for every batch of the testing dataset

        Args:
            input_image (tf.Tensor): RGB image
            target (tf.Tensor): Attention map from th GT
            step (int): train step
            epoch (int): Train epoch
        '''
        # Test Forward
        gen_output = self.generator(input_image, training=False)

        # Calculate metrics
        l1_metric = tf.reduce_mean(tf.abs(target - gen_output))
        kld_metric, mae_metric, mse_metric = self.calculate_metrics(target,
                                                                    gen_output)

        # Write logs for Tensorboard 
        tensorboard_step = step * self.BATCH_SIZE + epoch * self.TOTAL_IMGS_TEST
        with self.summary_writer.as_default():
            tf.summary.scalar('l1_metric_test', 
                              l1_metric, 
                              step = tensorboard_step)
            tf.summary.scalar('kld_metric_test',
                              kld_metric,
                              step = tensorboard_step)
            tf.summary.scalar('mae_metric_test',
                              mae_metric, 
                              step = tensorboard_step)
            tf.summary.scalar('mse_metric_test', 
                              mse_metric, 
                              step = tensorboard_step)

    def test(self, test_ds: tf.data.Dataset, epoch: int) -> None:
        '''Test function to pass the batchs from the dataset to the train step
        procedure

        Args:
            test_ds (tf.data.Dataset): Testing dataset
            epoch (int): Epoch
        '''
        # Iterate the test dataset in batches for testing
        for step, (input_image, target) in tqdm(test_ds.enumerate()):
            self.test_step(input_image, target, step, epoch)

    def dataset_pipeline(self, dataloader: Dataloader) -> None:
        '''Dataloader pipeline to create training and testing dataset

        Args:
            dataloader (Dataloader): dataloader class 
        '''
        
        # List all the RGB images in the training dataset to create the Dataset
        PATH = 'dataset/BDDA/'
        image_path = str(PATH + 'training/camera_images/all_images/*.jpg')
        self.train_dataset = tf.data.Dataset.list_files(image_path)
        # Shuffle the images, do this before mapping to use the maximun buffer 
        self.train_dataset = self.train_dataset.shuffle(self.BUFFER_SIZE)
        # Get the RGB images and the attention map from the dataset
        self.train_dataset = self.train_dataset.map(
            dataloader.load_image_train,
            num_parallel_calls=tf.data.AUTOTUNE)  
        # Create batches with the predefined batch size   
        self.train_dataset = self.train_dataset.batch(self.BATCH_SIZE)

        # Do the same with the testing set, but the shuffle procedure
        image_path_test = str(PATH + 'test/camera_images/all_images/*.jpg')
        self.test_dataset = tf.data.Dataset.list_files(image_path_test)
        self.test_dataset = self.test_dataset.map(dataloader.load_image_test)
        self.test_dataset = self.test_dataset.batch(self.BATCH_SIZE)

    def main(self) -> None:
        '''Main funtion
        '''
        self.fit(self.train_dataset, self.test_dataset)


if __name__ == "__main__":
    aragan = ARAGAN()
    dataloader = Dataloader(aragan.IMG_WIDTH, 
                            aragan.IMG_HEIGHT, 
                            aragan.OUTPUT_CHANNELS)
    aragan.dataset_pipeline(dataloader)
    aragan.fit(aragan.train_dataset, aragan.test_dataset)  