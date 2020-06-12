#!/usr/bin/env python3

import json
from pathlib import Path
import platform
import os
import subprocess
from time import time, sleep, monotonic

import cv2
import numpy as np

import depthai
print('Using depthai module from: ', depthai.__file__)

import consts.resource_paths
from depthai_helpers import utils
from depthai_helpers.cli_utils import cli_print, parse_args, PrintColors



def show_tracklets(tracklets, frame):
    # img_h = frame.shape[0]
    # img_w = frame.shape[1]

    # iterate through pre-saved entries & draw rectangle & text on image:
    tracklet_nr = tracklets.getNrTracklets()

    for i in range(tracklet_nr):
        tracklet        = tracklets.getTracklet(i)
        left_coord      = tracklet.getLeftCoord()
        top_coord       = tracklet.getTopCoord()
        right_coord     = tracklet.getRightCoord()
        bottom_coord    = tracklet.getBottomCoord()
        tracklet_id     = tracklet.getId()
        tracklet_label  = labels[tracklet.getLabel()]
        tracklet_status = tracklet.getStatus()

        # print("left: {0} top: {1} right: {2}, bottom: {3}, id: {4}, label: {5}, status: {6} "\
        #     .format(left_coord, top_coord, right_coord, bottom_coord, tracklet_id, tracklet_label, tracklet_status))
        
        pt1 = left_coord,  top_coord
        pt2 = right_coord,  bottom_coord
        color = (255, 0, 0) # bgr
        cv2.rectangle(frame, pt1, pt2, color)

        middle_pt = (int)(left_coord + (right_coord - left_coord)/2), (int)(top_coord + (bottom_coord - top_coord)/2)
        cv2.circle(frame, middle_pt, 0, color, -1)
        cv2.putText(frame, "ID {0}".format(tracklet_id), middle_pt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        x1, y1 = left_coord,  bottom_coord


        pt_t1 = x1, y1 - 40
        cv2.putText(frame, tracklet_label, pt_t1, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        pt_t2 = x1, y1 - 20
        cv2.putText(frame, tracklet_status, pt_t2, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)


        
    return frame

def decode_mobilenet_ssd(nnet_packet):
    detections = []
    # the result of the MobileSSD has detection rectangles (here: entries), and we can iterate through them
    for _, e in enumerate(nnet_packet.entries()):
        # for MobileSSD entries are sorted by confidence
        # {id == -1} or {confidence == 0} is the stopper (special for OpenVINO models and MobileSSD architecture)
        if e[0]['id'] == -1.0 or e[0]['confidence'] == 0.0 or e[0]['label'] > len(labels):
            break
        # save entry for further usage (as image package may arrive not the same time as nnet package)
        # the lower confidence threshold - the more we get false positives
        if e[0]['confidence'] > config['depth']['confidence_threshold']:
            # Temporary workaround: create a copy of NN data, due to issues with C++/python bindings
            copy = {}
            copy[0] = {}
            copy[0]['id']         = e[0]['id']
            copy[0]['left']       = e[0]['left']
            copy[0]['top']        = e[0]['top']
            copy[0]['right']      = e[0]['right']
            copy[0]['bottom']     = e[0]['bottom']
            copy[0]['label']      = e[0]['label']
            copy[0]['confidence'] = e[0]['confidence']
            if config['ai']['calc_dist_to_bb']:
                copy[0]['distance_x'] = e[0]['distance_x']
                copy[0]['distance_y'] = e[0]['distance_y']
                copy[0]['distance_z'] = e[0]['distance_z']
            detections.append(copy)
    return detections


def nn_to_depth_coord(x, y):
    x_depth = int(nn2depth['off_x'] + x * nn2depth['max_w'])
    y_depth = int(nn2depth['off_y'] + y * nn2depth['max_h'])
    return x_depth, y_depth

def average_depth_coord(pt1, pt2):
    factor = 1 - config['depth']['padding_factor']
    x_shift = int((pt2[0] - pt1[0]) * factor / 2)
    y_shift = int((pt2[1] - pt1[1]) * factor / 2)
    avg_pt1 = (pt1[0] + x_shift), (pt1[1] + y_shift)
    avg_pt2 = (pt2[0] - x_shift), (pt2[1] - y_shift)
    return avg_pt1, avg_pt2

def show_mobilenet_ssd(entries_prev, frame, is_depth=0):
    img_h = frame.shape[0]
    img_w = frame.shape[1]
    global config
    # iterate through pre-saved entries & draw rectangle & text on image:
    for e in entries_prev:
        if is_depth:
            pt1 = nn_to_depth_coord(e[0]['left'],  e[0]['top'])
            pt2 = nn_to_depth_coord(e[0]['right'], e[0]['bottom'])
            color = (255, 0, 0) # bgr
            avg_pt1, avg_pt2 = average_depth_coord(pt1, pt2)
            cv2.rectangle(frame, avg_pt1, avg_pt2, color)
            color = (255, 255, 255) # bgr
        else:
            pt1 = int(e[0]['left']  * img_w), int(e[0]['top']    * img_h)
            pt2 = int(e[0]['right'] * img_w), int(e[0]['bottom'] * img_h)
            color = (0, 0, 255) # bgr

        x1, y1 = pt1

        cv2.rectangle(frame, pt1, pt2, color)
        # Handles case where TensorEntry object label is out if range
        if e[0]['label'] > len(labels):
            print("Label index=",e[0]['label'], "is out of range. Not applying text to rectangle.")
        else:
            pt_t1 = x1, y1 + 20
            cv2.putText(frame, labels[int(e[0]['label'])], pt_t1, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            pt_t2 = x1, y1 + 40
            cv2.putText(frame, '{:.2f}'.format(100*e[0]['confidence']) + ' %', pt_t2, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color)
            if config['ai']['calc_dist_to_bb']:
                pt_t3 = x1, y1 + 60
                cv2.putText(frame, 'x:' '{:7.3f}'.format(e[0]['distance_x']) + ' m', pt_t3, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color)

                pt_t4 = x1, y1 + 80
                cv2.putText(frame, 'y:' '{:7.3f}'.format(e[0]['distance_y']) + ' m', pt_t4, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color)

                pt_t5 = x1, y1 + 100
                cv2.putText(frame, 'z:' '{:7.3f}'.format(e[0]['distance_z']) + ' m', pt_t5, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color)
    return frame

def decode_age_gender_recognition(nnet_packet):
    detections = []
    for _, e in enumerate(nnet_packet.entries()):
        if e[1]["female"] > 0.8 or e[1]["male"] > 0.8:
            detections.append(e[0]["age"])  
            if e[1]["female"] > e[1]["male"]:
                detections.append("female")
            else:
                detections.append("male")
    return detections

def show_age_gender_recognition(entries_prev, frame):
    # img_h = frame.shape[0]
    # img_w = frame.shape[1]
    if len(entries_prev) != 0:
        age = (int)(entries_prev[0]*100)
        cv2.putText(frame, "Age: " + str(age), (0, 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        gender = entries_prev[1]
        cv2.putText(frame, "G: " + str(gender), (0, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    frame = cv2.resize(frame, (300, 300))
    return frame

def decode_emotion_recognition(nnet_packet):
    detections = []
    for i in range(len(nnet_packet.entries()[0][0])):
        detections.append(nnet_packet.entries()[0][0][i])
    return detections

def show_emotion_recognition(entries_prev, frame):
    # img_h = frame.shape[0]
    # img_w = frame.shape[1]
    e_states = {
        0 : "neutral",
        1 : "happy",
        2 : "sad",
        3 : "surprise",
        4 : "anger"
    }
    if len(entries_prev) != 0:
        max_confidence = max(entries_prev)
        if(max_confidence > 0.7):
            emotion = e_states[np.argmax(entries_prev)]
            cv2.putText(frame, emotion, (10, 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    frame = cv2.resize(frame, (300, 300))

    return frame


def decode_landmarks_recognition(nnet_packet):
    landmarks = []
    for i in range(len(nnet_packet.entries()[0][0])):
        landmarks.append(nnet_packet.entries()[0][0][i])
    
    landmarks = list(zip(*[iter(landmarks)]*2))
    return landmarks

def show_landmarks_recognition(entries_prev, frame):
    img_h = frame.shape[0]
    img_w = frame.shape[1]

    if len(entries_prev) != 0:
        for i in entries_prev:
            try:
                x = int(i[0]*img_h)
                y = int(i[1]*img_w)
            except:
                continue
            # # print(x,y)
            cv2.circle(frame, (x,y), 3, (0, 0, 255))

    frame = cv2.resize(frame, (300, 300))

    return frame

global args
try:
    args = vars(parse_args())
except:
    os._exit(2)

 
stream_list = args['streams']

if args['config_overwrite']:
    args['config_overwrite'] = json.loads(args['config_overwrite'])

print("Using Arguments=",args)

usb2_mode = False
if args['force_usb2']:
    cli_print("FORCE USB2 MODE", PrintColors.WARNING)
    usb2_mode = True
else:
    usb2_mode = False

debug_mode = False
cmd_file = ''
if args['dev_debug'] == None:
    # Debug -debug flag NOT present,
    debug_mode = False
elif args['dev_debug'] == '':
    # If just -debug flag is present -> cmd_file = '' (wait for device to be connected beforehand)
    debug_mode = True
    cmd_file = ''
else: 
    debug_mode = True
    cmd_file = args['dev_debug']


calc_dist_to_bb = True
if args['disable_depth']:
    calc_dist_to_bb = False

decode_nn=decode_mobilenet_ssd
show_nn=show_mobilenet_ssd

if args['cnn_model'] == 'age-gender-recognition-retail-0013':
    decode_nn=decode_age_gender_recognition
    show_nn=show_age_gender_recognition
    calc_dist_to_bb=False

if args['cnn_model'] == 'emotions-recognition-retail-0003':
    decode_nn=decode_emotion_recognition
    show_nn=show_emotion_recognition
    calc_dist_to_bb=False

if args['cnn_model'] in ['facial-landmarks-35-adas-0002', 'landmarks-regression-retail-0009']:
    decode_nn=decode_landmarks_recognition
    show_nn=show_landmarks_recognition
    calc_dist_to_bb=False

if args['cnn_model']:
    cnn_model_path = consts.resource_paths.nn_resource_path + args['cnn_model']+ "/" + args['cnn_model']
    blob_file = cnn_model_path + ".blob"
    suffix=""
    if calc_dist_to_bb:
        suffix="_depth"
    blob_file_config = cnn_model_path + suffix + ".json"

blob_file_path = Path(blob_file)
blob_file_config_path = Path(blob_file_config)
if not blob_file_path.exists():
    cli_print("\nWARNING: NN blob not found in: " + blob_file, PrintColors.WARNING)
    os._exit(1)

if not blob_file_config_path.exists():
    cli_print("\nWARNING: NN json not found in: " + blob_file_config, PrintColors.WARNING)
    os._exit(1)

with open(blob_file_config) as f:
    data = json.load(f)

try:
    labels = data['mappings']['labels']
except:
    print("Labels not found in json!")


#print('depthai.__version__ == %s' % depthai.__version__)
#print('depthai.__dev_version__ == %s' % depthai.__dev_version__)

if platform.system() == 'Linux':
    ret = subprocess.call(['grep', '-irn', 'ATTRS{idVendor}=="03e7"', '/etc/udev/rules.d'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if(ret != 0):
        cli_print("\nWARNING: Usb rules not found", PrintColors.WARNING)
        cli_print("\nSet rules: \n"
        """echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | sudo tee /etc/udev/rules.d/80-movidius.rules \n"""
        "sudo udevadm control --reload-rules && udevadm trigger \n"
        "Disconnect/connect usb cable on host! \n", PrintColors.RED)
        os._exit(1)

device = None
if debug_mode: 
    print('Cmd file: ', cmd_file, ' args["device_id"]: ', args['device_id'])
    device = depthai.Device(cmd_file, args['device_id'])
else:
    device = depthai.Device(args['device_id'], usb2_mode)

#if not depthai.init_device(cmd_file, args['device_id']):
#    print("Error initializing device. Try to reset it.")
#    exit(1)


print('Available streams: ' + str(device.get_available_streams()))


# Append video stream if video recording was requested and stream is not already specified
video_file = None
if args['video'] is not None:
    stream_names = [stream if isinstance(stream, str) else stream['name'] for stream in stream_list]
    # open video file
    try:
        video_file = open(args['video'], 'wb')
        if 'video' not in stream_names:
            stream_list.append({"name": "video"})
    except IOError:
        print("Error: couldn't open video file for writing. Disabled video output stream")
        if 'video' in stream_names:
            stream_list.remove({"name": "video"})



# Do not modify the default values in the config Dict below directly. Instead, use the `-co` argument when running this script.
config = {
    # Possible streams:
    # ['left', 'right', 'jpegout', 'video', 'previewout', 'metaout', 'depth_sipp', 'disparity', 'depth_color_h']
    # If "left" is used, it must be in the first position.
    # To test depth use:
    # 'streams': [{'name': 'depth_sipp', "max_fps": 12.0}, {'name': 'previewout', "max_fps": 12.0}, ],
    'streams': stream_list,
    'depth':
    {
        'calibration_file': consts.resource_paths.calib_fpath,
        'padding_factor': 0.3,
        'depth_limit_m': 10.0, # In meters, for filtering purpose during x,y,z calc
        'confidence_threshold' : 0.5, #Depth is calculated for bounding boxes with confidence higher than this number 
    },
    'ai':
    {
        'blob_file': blob_file,
        'blob_file_config': blob_file_config,
        'calc_dist_to_bb': calc_dist_to_bb,
        'keep_aspect_ratio': not args['full_fov_nn'],
        'camera_input': args['cnn_camera'],
    },
    # object tracker
    'ot':
    {
        'max_tracklets'        : 20, #maximum 20 is supported
        'confidence_threshold' : 0.5, #object is tracked only for detections over this threshold
    },
    # object tracker
    'ot':
    {
        'max_tracklets'        : 20, #maximum 20 is supported
        'confidence_threshold' : 0.5, #object is tracked only for detections over this threshold
    },
    'board_config':
    {
        'swap_left_and_right_cameras': args['swap_lr'], # True for 1097 (RPi Compute) and 1098OBC (USB w/onboard cameras)
        'left_fov_deg': args['field_of_view'], # Same on 1097 and 1098OBC
        'rgb_fov_deg': args['rgb_field_of_view'],
        'left_to_right_distance_cm': args['baseline'], # Distance between stereo cameras
        'left_to_rgb_distance_cm': args['rgb_baseline'], # Currently unused
        'store_to_eeprom': args['store_eeprom'],
        'clear_eeprom': args['clear_eeprom'],
        'override_eeprom': args['override_eeprom'],
    },
    
    #'video_config':
    #{
    #    'rateCtrlMode': 'cbr', # Options: cbr / vbr
    #    'profile': 'h265_main', # Options: 'h264_baseline' / 'h264_main' / 'h264_high' / 'h265_main / 'mjpeg' '
    #    'bitrate': 8000000, # When using CBR (H264/H265 only)
    #    'maxBitrate': 8000000, # When using CBR (H264/H265 only)
    #    'keyframeFrequency': 30, (H264/H265 only)
    #    'numBFrames': 0, (H264/H265 only)
    #    'quality': 80 # (0 - 100%) When using VBR or MJPEG profile
    #}
    #'video_config':
    #{
    #    'profile': 'mjpeg',
    #    'quality': 95
    #}
}

if args['board']:
    board_path = Path(args['board'])
    if not board_path.exists():
        board_path = Path(consts.resource_paths.boards_dir_path) / Path(args['board'].upper()).with_suffix('.json')
        if not board_path.exists():
            print('ERROR: Board config not found: {}'.format(board_path))
            os._exit(2)
    with open(board_path) as fp:
        board_config = json.load(fp)
    utils.merge(board_config, config)
if args['config_overwrite'] is not None:
    config = utils.merge(args['config_overwrite'],config)
    print("Merged Pipeline config with overwrite",config)

if 'depth_sipp' in config['streams'] and ('depth_color_h' in config['streams'] or 'depth_mm_h' in config['streams']):
    print('ERROR: depth_sipp is mutually exclusive with depth_color_h')
    exit(2)
    # del config["streams"][config['streams'].index('depth_sipp')]



# Create a list of enabled streams ()
stream_names = [stream if isinstance(stream, str) else stream['name'] for stream in stream_list]

enable_object_tracker = 'object_tracker' in stream_names

# create the pipeline, here is the first connection with the device
p = device.create_pipeline(config=config)

if p is None:
    print('Pipeline is not created.')
    exit(3)

nn2depth = device.get_nn_to_depth_bbox_mapping()


nnet_prev = {}
nnet_prev["entries_prev"] = {}
nnet_prev["nnet_source"] = {}

t_start = time()
frame_count = {}
frame_count_prev = {}
entries_prev = {}
for s in stream_names:
    frame_count[s] = 0
    frame_count_prev[s] = 0
    stream_windows = []
    if s == 'previewout':
        for cam in {'rgb', 'left', 'right'}:
            nnet_prev['entries_prev'][cam] = []
            stream_windows.append(s + '-' + cam)
    else:
        stream_windows.append(s)
    for w in stream_windows:
        frame_count[w] = 0
        frame_count_prev[w] = 0
tracklets = None

process_watchdog_timeout=10 #seconds
def reset_process_wd():
    global wd_cutoff
    wd_cutoff=monotonic()+process_watchdog_timeout
    return

reset_process_wd()

ops = 0
prevTime = time()
while True:
    # retreive data from the device
    # data is stored in packets, there are nnet (Neural NETwork) packets which have additional functions for NNet result interpretation
    nnet_packets, data_packets = p.get_available_nnet_and_data_packets(True)
    
    ### Uncomment to print ops
    # ops = ops + 1
    # if time() - prevTime > 1.0:
    #     print('OPS: ', ops)
    #     ops = 0
    #     prevTime = time()

    packets_len = len(nnet_packets) + len(data_packets)
    if packets_len != 0:
        reset_process_wd()
    else:
        cur_time=monotonic()
        if cur_time > wd_cutoff:
            print("process watchdog timeout")
            os._exit(10)

    for _, nnet_packet in enumerate(nnet_packets):
        meta = nnet_packet.getMetadata()
        camera = 'rgb'
        if meta != None:
            camera = meta.getCameraName()
        nnet_prev["nnet_source"][camera] = nnet_packet
        nnet_prev["entries_prev"][camera] = decode_nn(nnet_packet)


    for packet in data_packets:
        window_name = packet.stream_name
        if packet.stream_name not in stream_names:
            continue # skip streams that were automatically added
        packetData = packet.getData()
        if packetData is None:
            print('Invalid packet data!')
            continue
        elif packet.stream_name == 'previewout':
            meta = packet.getMetadata()
            camera = 'rgb'
            if meta != None:
                camera = meta.getCameraName()

            window_name = 'previewout-' + camera
            # the format of previewout image is CHW (Chanel, Height, Width), but OpenCV needs HWC, so we
            # change shape (3, 300, 300) -> (300, 300, 3)
            data0 = packetData[0,:,:]
            data1 = packetData[1,:,:]
            data2 = packetData[2,:,:]
            frame = cv2.merge([data0, data1, data2])

            nn_frame = show_nn(nnet_prev["entries_prev"][camera], frame)
            if enable_object_tracker and tracklets is not None:
                nn_frame = show_tracklets(tracklets, nn_frame)
            cv2.putText(nn_frame, "fps: " + str(frame_count_prev[window_name]), (25, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0))
            cv2.imshow(window_name, nn_frame)
        elif packet.stream_name == 'left' or packet.stream_name == 'right' or packet.stream_name == 'disparity':
            frame_bgr = packetData
            cv2.putText(frame_bgr, packet.stream_name, (25, 25), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0))
            cv2.putText(frame_bgr, "fps: " + str(frame_count_prev[window_name]), (25, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0))
            if args['draw_bb_depth']:
                camera = packet.getMetadata().getCameraName()
                show_nn(nnet_prev["entries_prev"][camera], frame_bgr, is_depth=True)
            cv2.imshow(window_name, frame_bgr)
        elif packet.stream_name.startswith('depth'):
            frame = packetData

            if len(frame.shape) == 2:
                if frame.dtype == np.uint8: # grayscale
                    cv2.putText(frame, packet.stream_name, (25, 25), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255))
                    cv2.putText(frame, "fps: " + str(frame_count_prev[window_name]), (25, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255))
                else: # uint16
                    frame = (65535 // frame).astype(np.uint8)
                    #colorize depth map, comment out code below to obtain grayscale
                    frame = cv2.applyColorMap(frame, cv2.COLORMAP_HOT)
                    # frame = cv2.applyColorMap(frame, cv2.COLORMAP_JET)
                    cv2.putText(frame, packet.stream_name, (25, 25), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 255)
                    cv2.putText(frame, "fps: " + str(frame_count_prev[window_name]), (25, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 255)
            else: # bgr
                cv2.putText(frame, packet.stream_name, (25, 25), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255))
                cv2.putText(frame, "fps: " + str(frame_count_prev[window_name]), (25, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 255)

            if args['draw_bb_depth']:
                # TODO check NN cam input
                show_nn(nnet_prev["entries_prev"]['right'], frame, is_depth=True)
            cv2.imshow(window_name, frame)

        elif packet.stream_name == 'jpegout':
            jpg = packetData
            mat = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
            cv2.imshow('jpegout', mat)

        elif packet.stream_name == 'video':
            videoFrame = packetData
            videoFrame.tofile(video_file)
            #mjpeg = packetData
            #mat = cv2.imdecode(mjpeg, cv2.IMREAD_COLOR)
            #cv2.imshow('mjpeg', mat)

        elif packet.stream_name == 'meta_d2h':
            str_ = packet.getDataAsStr()
            dict_ = json.loads(str_)

            print('meta_d2h Temp',
                ' CSS:' + '{:6.2f}'.format(dict_['sensors']['temperature']['css']),
                ' MSS:' + '{:6.2f}'.format(dict_['sensors']['temperature']['mss']),
                ' UPA:' + '{:6.2f}'.format(dict_['sensors']['temperature']['upa0']),
                ' DSS:' + '{:6.2f}'.format(dict_['sensors']['temperature']['upa1']))            
        elif packet.stream_name == 'object_tracker':
            tracklets = packet.getObjectTracker()

        frame_count[window_name] += 1

    t_curr = time()
    if t_start + 1.0 < t_curr:
        t_start = t_curr

        for s in stream_names:
            stream_windows = []
            if s == 'previewout':
                for cam in {'rgb', 'left', 'right'}:
                    stream_windows.append(s + '-' + cam)
            else:
                stream_windows.append(s)
            for w in stream_windows:
                frame_count_prev[w] = frame_count[w]
                frame_count[w] = 0

    key = cv2.waitKey(1)
    if key == ord('c'):
        if 'jpegout' in stream_names == 0:
            print("'jpegout' stream not enabled. Try settings -s jpegout to enable it")
        else:
            device.request_jpeg()
    elif key == ord('f'):
        device.request_af_trigger()
    elif key == ord('1'):
        device.request_af_mode(depthai.AutofocusMode.AF_MODE_AUTO)
    elif key == ord('2'):
        device.request_af_mode(depthai.AutofocusMode.AF_MODE_CONTINUOUS_VIDEO)
    elif key == ord('q'):
        break


del p  # in order to stop the pipeline object should be deleted, otherwise device will continue working. This is required if you are going to add code after the main loop, otherwise you can ommit it.
device.deinit_device()

# Close video output file if was opened
if video_file is not None:
    video_file.close()

print('py: DONE.')
