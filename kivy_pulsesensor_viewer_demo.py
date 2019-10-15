'''
This example uses a Kivy Garden Graph package to draw Arduino pulse sensor data on a PC/phone screen. We use PySerial(PC) or usbserial4a(Android) to receive pulse sensor data from an Arduino board.
To read more about this demo, visit: https://github.com/EveryWhereLab/kivy-pulsesensor-viewer-demo/.
'''

import kivy
kivy.require('1.8.0')
from kivy.app import App
from kivy.properties import ObjectProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.utils import platform
from kivy.properties import BooleanProperty
from kivy.uix.label import Label
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.uix.behaviors import FocusBehavior
from kivy.uix.recycleview.layout import LayoutSelectionBehavior
from kivy.uix.button import Button
from queue import Queue
import threading
import re
if platform == 'android':
    from usb4a import usb
    from usbserial4a import serial4a
    from usbserial4a import cdcacmserial4a
else:
    from serial.tools import list_ports
    from serial import Serial
from kivy.garden.graph import Graph, MeshLinePlot
from kivy.uix.popup import Popup
from kivy.properties import ListProperty, StringProperty, ObjectProperty

class SelectableRecycleBoxLayout(FocusBehavior, LayoutSelectionBehavior,
                                 RecycleBoxLayout):
    ''' Adds selection and focus behaviour to the view. '''
  
class SelectableLabel(RecycleDataViewBehavior, Label):
    ''' Add selection support to the Label '''
    index = None
    selected = BooleanProperty(False)
    selectable = BooleanProperty(True)

    def refresh_view_attrs(self, rv, index, data):
        ''' Catch and handle the view changes '''
        self.index = index
        return super(SelectableLabel, self).refresh_view_attrs(
            rv, index, data)

    def on_touch_down(self, touch):
        ''' Add selection on touch down '''
        if super(SelectableLabel, self).on_touch_down(touch):
            return True
        if self.collide_point(*touch.pos) and self.selectable:
            return self.parent.select_with_touch(self.index, touch)

    def apply_selection(self, rv, index, is_selected):
        ''' Respond to the selection of items in the view. '''
        self.selected = is_selected
        if is_selected:
            print("selection changed to {0}".format(rv.data[index]))
            rv.selectedItem  = index
        else:
            print("selection removed for {0}".format(rv.data[index]))

class RV(RecycleView):
    def __init__(self, **kwargs):
        super(RV, self).__init__(**kwargs)
        self.selectedItem = -1
        self.device_name_list=[]
    def append_item(self, x) :
        self.device_name_list.append(x[0])
        self.data.append({'text': str(x[1])})

    def set_first_item_as_default(self):
        if len(self.data) > 0:
            self.selectedItem = 0
            if len(self.view_adapter.views) > 0:
                self.view_adapter.views[self.selectedItem].selected = 1

    def get_first_selected_device_name(self):
         if self.selectedItem in range(len(self.device_name_list)):
            return self.device_name_list[self.selectedItem]
         return None

    def clearAll(self):
        self.device_name_list.clear()
        self.selectedItem = -1
        self.view_adapter.views.clear()

class PulseSensorViewerDemo(BoxLayout):
    def __init__(self):
        self.rx_temp_data = ""
        self.points = [(0,0)]
        self.samples = Queue(300)
        self.serial_port = None
        self.read_thread = None
        self.port_thread_lock = threading.Lock()
        super(PulseSensorViewerDemo, self).__init__()
        self.reading_thread_enabled = False
        self.graph = self.ids.graph_plot
        self.plot = []
        self.plot.append(MeshLinePlot(color=[1, 1, 0, 1]))  #  - Yellow
        self.reset_plots()
        for plot in self.plot:
            self.graph.add_plot(plot)
        self.do_schedule_scan_once()

    def reset_plots(self):
        for plot in self.plot:
            plot.points = [(0, 0)]
        self.counter = 1

    def do_start_stop_toggle(self):
        try:
            if not self.reading_thread_enabled:
                # to open the serial port, start a reading thread, and schedule a drawing timer
                selected_device = self.ids.device_list.get_first_selected_device_name()
                if selected_device != None:
                    if platform == 'android':
                         device = usb.get_usb_device(selected_device)
                         if not device:
                            raise SerialException(
                            "Device {} not present!".format(selected_device)
                            )
                         if not usb.has_usb_permission(device):
                            usb.request_usb_permission(device)
                            return
                         self.serial_port = serial4a.get_serial_port(
                             selected_device,
                             9600,
                             8,
                             'N',
                             1,
                             timeout=1
                          )
                    else:
                        self.serial_port = Serial(
                            selected_device,
                            9600,
                            8,
                            'N',
                            1,
                            timeout=1
                        )
                    if self.serial_port.is_open and not self.read_thread:
                        self.serial_port.reset_input_buffer()
                        self.read_thread = threading.Thread(target = self.read_serial_msg_thread)
                        self.reading_thread_enabled = True
                        self.read_thread.start()
                        # Since there is a queue to adjust inconsistent throughputs, we can set a small time interval to check if sampes exist in queue .
                        Clock.schedule_interval(self.draw_waveform, 1 / 50.)
                        self.ids.toggle_button.text = "Stop acquisition"
                else:
                    # create content and add to the popup
                    content = Button(text='No device selected. Close me!')
                    popup = Popup(title='Reminder', content=content, auto_dismiss=False)
                    # bind the on_press event of the button to the dismiss function
                    content.bind(on_press=popup.dismiss)
                    # open the popup
                    popup.open()
            else:
                # to unschedule the drawing timer and stop the reading thread
                self.reset_plots()
                Clock.unschedule(self.draw_waveform)
                self.reading_thread_enabled = False
                with self.port_thread_lock:
                    self.serial_port.close()
                self.read_thread.join()
                self.read_thread = None
                self.ids.toggle_button.text = "Start acquisition"
        except NotImplementedError:
            popup = ErrorPopup()
            popup.open()

    def do_schedule_scan_once(self):
        Clock.schedule_once(self.scan_usb_devices,1/10)

    def scan_usb_devices(self,dt):
        self.ids.device_list.clearAll()
        device_node_list = []
        r = lambda x: x if x is not None else ''
        if platform == 'android':
            usb_device_list = usb.get_usb_device_list()
            device_node_list = [
                (device.getDeviceName(), r(device.getProductName()) + "(vid=" + str(device.getVendorId()) + ",pid=" + str(device.getProductId()) + ")" ) for device in usb_device_list
            ]
        else:
            usb_device_list = list_ports.comports()
            device_node_list = [(device.device, r(device.product) + "(vid=" + str(device.vid) + ",pid=" + str(device.pid) + ")") for device in usb_device_list]
        if len(device_node_list) > 0:
            for device in device_node_list:
                self.ids.device_list.append_item(device)
            Clock.schedule_once(self.set_first_item_as_default,1/10)
                
    def set_first_item_as_default(self, dt):
        self.ids.device_list.set_first_item_as_default()

    def draw_waveform(self,dt): 
        update_size = self.samples.qsize()
        if update_size == 0:
            return
        if update_size > 200:
            # Just show latest samples. 
            while(self.samples.qsize() > 200):
                self.samples.get()
            self.plot[0].points.clear()
            update_size = 200
            self.counter = 0
        else:
            old_samples_to_remove = self.counter + update_size - 200
            if old_samples_to_remove > 0:
                # We re-write our points list if number of values exceed 200.
                # ie. Move each timestamp to the left.
                # We re-write our points list if number of values exceed 200.
                # ie. Move each timestamp to the left.
                for plot in self.plot:
                    del(plot.points[0: old_samples_to_remove-1])
                    plot.points[:] = [(i[0] - old_samples_to_remove, i[1]) for i in plot.points[:]]
                self.counter = 200 - old_samples_to_remove
        points = []
        for i in range(update_size):
            points.append((self.counter + i, int(self.samples.get())))
        self.plot[0].points.extend(points)
        self.counter += update_size
                       
    def get_lines(self, data):   
        lines = []
        self.rx_temp_data += data.decode("ascii")
        idx = self.rx_temp_data.rfind('\r\n')
        if idx > 0: 
            lines = re.split(r'[\n\r]+', self.rx_temp_data)
            if idx == len(self.rx_temp_data):
                self.rx_temp_data = ""
            else:
                self.rx_temp_data = self.rx_temp_data[idx + 2:]
        return lines
               
    def read_serial_msg_thread(self):
        while self.reading_thread_enabled == True:
            try:
                with self.port_thread_lock:
                    if not self.serial_port.is_open:
                        break
                    received_msg = self.serial_port.read(self.serial_port.in_waiting)
                if received_msg:
                    lines = self.get_lines(received_msg)
                    for line in lines:
                        if len(line) < 1:
                            continue
                        if line[0] == 'S':
                            # pluse sensor sample
                            result = re.findall(r'\d+', line[1:] )
                            if len(result) > 0:
                                self.samples.put(int(result[0]))
                        elif line[0] == 'B':
                            result = re.findall(r'\d+', line[1:] )
                            if len(result) > 0:
                                self.ids.BPM_data.text = "BPM=" + result[0]
                        elif line[0] == 'Q':
                            result = re.findall(r'\d+', line[1:] )
                            if len(result) > 0:
                                self.ids.IBI_data.text = "IBI=" + result[0]
                        elif line[0] == 'T':
                            # We add a temperature example here. If you have a temperature sensor, you can try to report temperature value with a prefix 'T' in your Arduino board.
                            result = re.findall(r"[-+]?\d*\.\d+|\d+", line[1:])
                            if len(result) > 0:
                                self.ids.Temperature_data.text = "Temperature=" + result[0]
            except Exception as ex:
                raise ex

class PulseSensorViewerDemoApp(App):
    def build(self):
        return PulseSensorViewerDemo()

    def on_pause(self):
        return True

    def on_stop(self):
        self.root.reading_thread_enabled = False
   
class ErrorPopup(Popup):
    pass

if __name__ == '__main__':
    PulseSensorViewerDemoApp().run()
